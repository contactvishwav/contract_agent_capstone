"""
Proves the real circuit breaker (backend/shared/reliability/circuit_
breaker.py) is actually wired into the three places production traffic
goes through it: LLMExtractionService.extract_clauses,
PolicyEvaluationService.evaluate_clause, and Neo4jContractRepository.
Each test patches that module's circuit breaker reference to a fresh,
low-threshold CircuitBreaker (not the shared GEMINI_/NEO4J_ singleton) so
opening it is fast and deterministic and can't leak into other tests.

The proof point in every test: after the breaker trips open, the
underlying fake dependency's call count stops increasing - later calls are
rejected by the breaker itself, without ever reaching the "failing"
dependency again. That is the actual behavior a working circuit breaker
provides that the previous (removed, confirmed non-functional) one never
could.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.agents.llm_extraction_service import LLMExtractionService
from backend.agents.policy_evaluation_service import PolicyEvaluationService
from backend.domain.policies.entities import PolicyRule
from backend.shared.cache.redis_cache import cache
from backend.shared.reliability.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.contract_repository import Neo4jContractRepository


class AlwaysFailsLLM:
    """Every .invoke() raises - simulates a real Gemini outage."""

    def __init__(self):
        self.call_count = 0
        self.model = "fake-model"

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, prompt):
        self.call_count += 1
        raise RuntimeError("simulated Gemini outage")


class LLMExtractionServiceCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client._cache.clear()
        self.breaker = CircuitBreaker("test_llm_extraction_gemini", failure_threshold=2, recovery_timeout_seconds=60)
        self._breaker_patcher = patch("backend.agents.llm_extraction_service.GEMINI_CIRCUIT_BREAKER", self.breaker)
        self._breaker_patcher.start()
        self._cache_patcher = patch("backend.agents.llm_extraction_service.Phase3Config.CACHE_ENABLED", False)
        self._cache_patcher.start()

    def tearDown(self):
        self._breaker_patcher.stop()
        self._cache_patcher.stop()
        cache.redis_client._cache.clear()

    def test_repeated_gemini_failures_open_the_breaker_and_stop_calling_it(self):
        llm = AlwaysFailsLLM()
        service = LLMExtractionService(llm)

        service.extract_clauses("first attempt")
        service.extract_clauses("second attempt")  # 2nd failure trips the breaker (threshold=2)
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(self.breaker.get_status()["state"], "open")

        result = service.extract_clauses("third attempt - circuit should be open")

        self.assertEqual(llm.call_count, 2, "the LLM must not be called again while the breaker is open")
        self.assertEqual(result, [], "extract_clauses degrades to [] by default rather than raising")

    def test_raise_on_error_surfaces_the_open_circuit_as_an_exception(self):
        llm = AlwaysFailsLLM()
        service = LLMExtractionService(llm)

        service.extract_clauses("first", raise_on_error=False)
        service.extract_clauses("second", raise_on_error=False)  # trips open

        with self.assertRaises(CircuitBreakerOpenError):
            service.extract_clauses("third", raise_on_error=True)
        self.assertEqual(llm.call_count, 2)


class PolicyEvaluationServiceCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client._cache.clear()
        self.breaker = CircuitBreaker("test_policy_eval_gemini", failure_threshold=2, recovery_timeout_seconds=60)
        self._breaker_patcher = patch("backend.agents.policy_evaluation_service.GEMINI_CIRCUIT_BREAKER", self.breaker)
        self._breaker_patcher.start()
        self._cache_patcher = patch("backend.agents.policy_evaluation_service.Phase3Config.CACHE_ENABLED", False)
        self._cache_patcher.start()
        self.rule = PolicyRule(
            id="rule_1", rule_text="No unlimited liability.", rule_type="mandatory",
            applies_to=["general"], severity="HIGH", section_reference="s1",
        )

    def tearDown(self):
        self._breaker_patcher.stop()
        self._cache_patcher.stop()
        cache.redis_client._cache.clear()

    def test_repeated_gemini_failures_open_the_breaker_and_stop_calling_it(self):
        llm = AlwaysFailsLLM()
        service = PolicyEvaluationService(llm)

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(self.breaker.get_status()["state"], "open")

        with self.assertRaises(CircuitBreakerOpenError):
            service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])

        self.assertEqual(llm.call_count, 2, "the LLM must not be called again while the breaker is open")


class FailingGraph:
    """Every .query() raises - simulates a real Aura outage."""

    def __init__(self):
        self.call_count = 0

    def query(self, cypher, params=None):
        self.call_count += 1
        raise RuntimeError("simulated Neo4j outage")


class Neo4jContractRepositoryCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client._cache.clear()
        self.breaker = CircuitBreaker("test_neo4j_repo", failure_threshold=2, recovery_timeout_seconds=60)
        self._breaker_patcher = patch("backend.infrastructure.contract_repository.NEO4J_CIRCUIT_BREAKER", self.breaker)
        self._breaker_patcher.start()

    def tearDown(self):
        self._breaker_patcher.stop()
        cache.redis_client._cache.clear()

    def _make_repo(self, graph):
        with patch("backend.infrastructure.contract_repository.graph", graph), \
             patch("backend.infrastructure.contract_repository.embedding", MagicMock()):
            return Neo4jContractRepository()

    def test_repeated_neo4j_failures_open_the_breaker_and_stop_calling_it(self):
        import asyncio

        graph = FailingGraph()
        repo = self._make_repo(graph)

        asyncio.run(repo.get_contract_by_id("c1", "tenant_1"))
        asyncio.run(repo.get_contract_by_id("c1", "tenant_1"))  # 2nd failure trips the breaker
        self.assertEqual(graph.call_count, 2)
        self.assertEqual(self.breaker.get_status()["state"], "open")

        result = asyncio.run(repo.get_contract_by_id("c1", "tenant_1"))

        self.assertEqual(graph.call_count, 2, "Neo4j must not be queried again while the breaker is open")
        self.assertIsNone(result, "get_contract_by_id treats a blocked call the same as any other failure - returns None")


if __name__ == "__main__":
    unittest.main()
