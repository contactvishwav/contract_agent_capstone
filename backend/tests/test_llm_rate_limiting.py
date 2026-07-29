"""
Regression tests for item 13 in docs/ENTERPRISE_READINESS.md's punch list:
zero rate limiting/concurrency control existed around LLM extraction calls,
and StepExecutor's retry loop would blindly retry a rate-limit (429) error
up to max_retries times, each attempt re-sending the full contract text/
clause set - silently tripling cost on a single transient failure with no
realistic chance of success (especially for a per-day quota wall, which
cannot be waited out within one request's lifetime).

Reuses the same asyncio.Semaphore *concept* already established elsewhere
in this codebase (optimized_cuad_tools.py, embedding_optimizer.py) - here as
a shared threading.Semaphore (backend/shared/utils/llm_concurrency.py)
since LLMExtractionService/PolicyEvaluationService are synchronous methods,
not async def.
"""

import threading
import time
import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.llm_extraction_service import LLMExtractionService, _LLMExtractionResponse
    from backend.agents.policy_evaluation_service import PolicyEvaluationService, _LLMPolicyEvaluationResponse
    from backend.domain.policies.entities import PolicyRule
    from backend.agents.planning.execution_engine import StepExecutor, ExecutionResult
    from backend.agents.planning.planning_agent import ExecutionStep, StepType


class FakeLLM:
    def __init__(self, response):
        self._response = response

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        return self._response


class ConcurrencySemaphoreAppliedTests(unittest.TestCase):
    """Confirms the shared llm_call_semaphore actually wraps the outbound
    .invoke() call in both services - patched with a MagicMock so the
    context-manager protocol (__enter__/__exit__) can be asserted on
    directly, rather than needing to prove real concurrency limiting via
    timing (which would be flaky in a test)."""

    def test_extraction_service_acquires_and_releases_semaphore(self):
        fake_semaphore = MagicMock()
        with patch("backend.agents.llm_extraction_service.llm_call_semaphore", fake_semaphore):
            service = LLMExtractionService(FakeLLM(_LLMExtractionResponse(clauses=[])))
            service.extract_clauses("Some contract text.")

        fake_semaphore.__enter__.assert_called_once()
        fake_semaphore.__exit__.assert_called_once()

    def test_policy_evaluation_service_acquires_and_releases_semaphore(self):
        rule = PolicyRule(id="r1", rule_text="text", rule_type="mandatory",
                           applies_to=["general"], severity="HIGH", section_reference="s1")
        fake_semaphore = MagicMock()
        with patch("backend.agents.policy_evaluation_service.llm_call_semaphore", fake_semaphore):
            service = PolicyEvaluationService(FakeLLM(_LLMPolicyEvaluationResponse(violations=[])))
            service.evaluate_clause("Non-Compete", "Some clause text.", [rule])

        fake_semaphore.__enter__.assert_called_once()
        fake_semaphore.__exit__.assert_called_once()


class RealSemaphoreLimitsConcurrencyTests(unittest.TestCase):
    """End-to-end proof (real threading.Semaphore, real threads, no mocks on
    the semaphore itself) that a limit of 1 actually serializes overlapping
    calls rather than just being present but inert."""

    def test_semaphore_of_one_serializes_two_concurrent_extractions(self):
        from backend.shared.utils.llm_concurrency import llm_call_semaphore

        overlap_detected = threading.Event()
        currently_inside = {"count": 0}
        lock = threading.Lock()

        class SlowFakeLLM:
            def with_structured_output(self, schema):
                return self

            def invoke(self, prompt):
                with lock:
                    currently_inside["count"] += 1
                    if currently_inside["count"] > 1:
                        overlap_detected.set()
                time.sleep(0.2)
                with lock:
                    currently_inside["count"] -= 1
                return _LLMExtractionResponse(clauses=[])

        # Temporarily tighten the shared semaphore to 1 for this test only.
        original_value = llm_call_semaphore._value
        test_semaphore = threading.Semaphore(1)
        with patch("backend.agents.llm_extraction_service.llm_call_semaphore", test_semaphore):
            service = LLMExtractionService(SlowFakeLLM())
            threads = [
                threading.Thread(target=service.extract_clauses, args=("text",))
                for _ in range(3)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        self.assertFalse(overlap_detected.is_set(), "Semaphore of 1 should have serialized all calls - none should overlap")


class RateLimitAwareRetryTests(unittest.TestCase):
    """StepExecutor must not blindly retry a rate-limit (429) error the way
    it retries ordinary transient failures."""

    def _make_step(self, step_type):
        return ExecutionStep(step_id="s1", step_type=step_type, description="test step")

    def test_resource_exhausted_fails_immediately_without_retry(self):
        from google.api_core.exceptions import ResourceExhausted

        executor = StepExecutor()
        call_count = {"n": 0}

        async def fake_execute_clause_extraction(step, context):
            call_count["n"] += 1
            raise ResourceExhausted("quota exceeded")

        executor._execute_clause_extraction = fake_execute_clause_extraction

        import asyncio
        result = asyncio.run(executor._execute_step_with_retry(self._make_step(StepType.EXTRACT_CLAUSES), {}))

        self.assertFalse(result.success)
        self.assertEqual(call_count["n"], 1, "Should attempt exactly once - no retry into a rate limit")
        self.assertIn("Rate limit", result.error_message)

    def test_ordinary_exception_still_retries_as_before(self):
        executor = StepExecutor()
        call_count = {"n": 0}

        async def flaky_then_succeeds(step, context):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("transient network blip")
            return []

        executor._execute_clause_extraction = flaky_then_succeeds

        import asyncio
        result = asyncio.run(executor._execute_step_with_retry(self._make_step(StepType.EXTRACT_CLAUSES), {}))

        self.assertTrue(result.success)
        self.assertEqual(call_count["n"], 2, "Ordinary (non-rate-limit) failures should still retry as before")


if __name__ == "__main__":
    unittest.main()
