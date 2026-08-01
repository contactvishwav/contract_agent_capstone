"""
Real, asserting tests replacing test_policy_system.py (punch-list item
22) - a zero-assert, unmocked async smoke script that was excluded from
CI (async def with no @pytest.mark.asyncio, no asserts, broad
except-Exception-and-print).

Scope check before writing these (don't duplicate, don't assume): of the
5 things test_policy_system.py exercised -
  1. PolicyChunkingStrategy.chunk_document - NOT covered elsewhere. Real
     tests below.
  2. PolicyChunkingAgent.execute - NOT covered elsewhere. Real test below.
  3. PolicyRepository.search_policies_semantic - ALREADY covered by
     test_vector_index_search.py::PolicyRepositoryVectorTests (real
     Cypher-shape/tenant-scoping assertions). Not duplicated here.
  4. ChunkingFactory.create_strategy('policy') - NOT covered elsewhere.
     Real test below.
  5. ChainOfThoughtAgent dynamic policy loading - _risk_assessment_chain
     unconditionally calls PolicyRepository.get_applicable_policies on
     every risk_assessment call (not gated behind an explicit tenant_id),
     so this is already exercised by
     test_pattern_integration.py::TestChainOfThoughtAgent::
     test_cot_agent_risk_assessment. Not duplicated here.

Writing real assertions for PolicyChunkingStrategy surfaced a genuine,
previously-undiscovered bug: the class never implemented
IChunkingStrategy's abstract get_chunk_size() method, so
PolicyChunkingStrategy() - and therefore
ChunkingFactory.create_strategy('policy'), the real path behind
PolicyChunkingAgent.execute() (POST /api/policies/upload's first real
step) - raised TypeError on construction, always. test_policy_system.py's
own "Test 1" would have hit this exact error, but its broad
except-Exception-and-print swallowed it silently. Fixed in
infrastructure/chunking/policy_strategy.py alongside these tests.
"""

import unittest
from unittest.mock import AsyncMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chunking.policy_strategy import PolicyChunkingStrategy
    from backend.infrastructure.chunking.factory import ChunkingFactory
    from backend.agents.policy_agents import PolicyChunkingAgent
    from backend.agents.supervisor.interfaces import AgentContext

SAMPLE_POLICY = """
1. LIABILITY POLICY

The Company shall not accept unlimited liability in any contract.
All contracts must include a liability cap of $1,000,000.
Indemnification clauses are prohibited unless approved by legal.

2. TERMINATION POLICY

All contracts must include a 30-day notice period for termination.
Immediate termination is prohibited except for material breach.
Termination clauses should specify post-termination obligations.
"""

_VALID_RULE_TYPES = {"mandatory", "recommended", "prohibited", "general"}
_VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


class PolicyChunkingStrategyTests(unittest.TestCase):
    def setUp(self):
        self.strategy = PolicyChunkingStrategy()

    def test_construction_does_not_raise(self):
        # The regression this whole file exists to catch: PolicyChunkingStrategy
        # was missing IChunkingStrategy's abstract get_chunk_size(), so this
        # line alone used to raise TypeError.
        PolicyChunkingStrategy()

    def test_get_chunk_size_returns_configured_max(self):
        strategy = PolicyChunkingStrategy(max_chunk_size=1500)
        self.assertEqual(strategy.get_chunk_size(), 1500)

    def test_chunk_document_extracts_real_rules_from_sample_policy(self):
        chunks = self.strategy.chunk_document(SAMPLE_POLICY)

        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            self.assertIn(chunk["rule_type"], _VALID_RULE_TYPES)
            self.assertIn(chunk["severity"], _VALID_SEVERITIES)
            self.assertEqual(chunk["chunk_type"], "policy_rule")
            self.assertTrue(chunk["content"])
            self.assertGreaterEqual(chunk["quality_score"], 0.0)
            self.assertLessEqual(chunk["quality_score"], 1.0)

    def test_liability_rule_tagged_with_liability_applicability(self):
        # "Indemnification" is one of _extract_applicable_types's liability
        # keywords - real behavior tags this rule 'liability' even though
        # the literal word "liability" isn't in this particular sentence.
        chunks = self.strategy.chunk_document(SAMPLE_POLICY)
        liability_chunks = [c for c in chunks if "liability" in c["applies_to"]]

        self.assertTrue(liability_chunks, "expected at least one chunk tagged applies_to=liability")

    def test_termination_rule_tagged_with_termination_applicability(self):
        chunks = self.strategy.chunk_document(SAMPLE_POLICY)
        termination_chunks = [c for c in chunks if "termination" in c["applies_to"]]

        self.assertTrue(termination_chunks, "expected at least one chunk tagged applies_to=termination")

    def test_prohibited_language_scores_high_or_critical_severity(self):
        chunks = self.strategy.chunk_document(SAMPLE_POLICY)
        prohibited_chunks = [c for c in chunks if c["rule_type"] == "prohibited"]

        self.assertTrue(prohibited_chunks, "expected at least one prohibited-type rule")
        for chunk in prohibited_chunks:
            self.assertIn(chunk["severity"], {"HIGH", "CRITICAL"})

    def test_empty_document_produces_no_chunks(self):
        self.assertEqual(self.strategy.chunk_document(""), [])


class ChunkingFactoryPolicyStrategyTests(unittest.TestCase):
    def test_create_strategy_policy_returns_policy_chunking_strategy(self):
        strategy = ChunkingFactory.create_strategy("policy")
        self.assertIsInstance(strategy, PolicyChunkingStrategy)

    def test_policy_strategy_listed_as_available(self):
        # Real gap surfaced while writing this test: get_available_strategies()
        # is a separately-hardcoded dict from create_strategy's own strategy
        # map, and had never included "policy" - create_strategy("policy")
        # worked, but nothing advertised it as an option. Fixed alongside
        # this test (infrastructure/chunking/factory.py).
        available = ChunkingFactory.get_available_strategies()
        self.assertIn("policy", available)


class PolicyChunkingAgentTests(unittest.TestCase):
    def test_execute_chunks_and_stores_a_real_policy_document(self):
        agent = PolicyChunkingAgent()
        agent.storage_service.store_chunks = AsyncMock(return_value={"chunks_stored": 3})

        context = AgentContext(
            input_data={
                "policy_text": SAMPLE_POLICY,
                "tenant_id": "tenant_a",
                "policy_name": "Test Liability Policy",
            },
            workflow_context=None,
        )

        result = agent.execute(context)

        self.assertEqual(result.status, "success")
        self.assertGreater(result.data["chunks_created"], 0)
        self.assertTrue(result.data["document_id"].startswith("policy_tenant_a_"))
        agent.storage_service.store_chunks.assert_awaited_once()

    def test_execute_reports_error_status_on_missing_required_field(self):
        agent = PolicyChunkingAgent()

        # No tenant_id in input_data.
        context = AgentContext(input_data={"policy_text": SAMPLE_POLICY}, workflow_context=None)

        result = agent.execute(context)

        self.assertEqual(result.status, "error")
        self.assertIn("error", result.data)


if __name__ == "__main__":
    unittest.main()
