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
  5. ChainOfThoughtAgent dynamic policy loading - moot: ChainOfThoughtAgent
     itself was removed shortly after this file was written (confirmed
     unreachable in real usage, output a dead side-channel, and its policy
     check had drifted into a third hardcoded keyword matcher duplicating
     PolicyEvaluationService - see docs/CAPSTONE_SUMMARY.md). Nothing to
     duplicate or cover here anymore.

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

    def test_real_rule_sentences_survive_intact_not_misidentified_as_headers(self):
        """Real, confirmed bug found live during a production verification:
        _identify_policy_sections's SHALL/MUST/REQUIRED/MANDATORY pattern
        matched ANY line starting with a capital letter containing one of
        those words anywhere before the first period - which describes
        nearly every real policy RULE sentence, not just headers. A real
        tenant-uploaded playbook (Contract_Policy_Playbook.pdf) lost
        almost all of its actual rule content this way: each real rule
        sentence immediately following a short header line was itself
        misidentified as the start of a NEW section, silently discarding
        the genuine header (empty content never gets appended to
        `sections`) and losing the rule's own text as an orphaned,
        content-less section title. Only 1 of ~10 real rules survived in
        the live reproduction. This uses the same shape as that real
        document: short "N. Title Standard" headers immediately followed
        (no blank line) by full rule sentences containing shall/must -
        the exact structure that triggered the bug."""
        text = (
            "1. Payment Terms Standard\n"
            "Payment terms shall be Net 30 or Net 45 days.\n"
            "Net 90 payment terms are prohibited, and payment must not be made contingent on Client satisfaction.\n"
            "2. Termination Notice Standard\n"
            "Termination for convenience shall require 30 to 60 days written notice from both parties.\n"
        )
        chunks = self.strategy.chunk_document(text)
        contents = [c["content"] for c in chunks]

        net_90_rule = next(
            (c for c in contents if "Net 90 payment terms are prohibited" in c), None
        )
        self.assertIsNotNone(
            net_90_rule,
            f"the real Net-90-prohibited rule sentence must survive as real chunk content, got: {contents}",
        )
        # The regression: this exact sentence used to become a section
        # TITLE with no content of its own (silently dropped, since only
        # non-empty-content sections get appended) because it starts with
        # a capital letter and contains "must" - the assertion above is
        # what actually catches that.

        termination_rule = next(
            (c for c in contents if "shall require 30 to 60 days written notice" in c), None
        )
        self.assertIsNotNone(
            termination_rule,
            f"the real termination-notice rule sentence must survive as real chunk content, got: {contents}",
        )

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


class PolicyChunkingAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_chunks_and_stores_a_real_policy_document(self):
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

        result = await agent.execute(context)

        self.assertEqual(result.status, "success")
        self.assertGreater(result.data["chunks_created"], 0)
        self.assertTrue(result.data["document_id"].startswith("policy_tenant_a_"))
        agent.storage_service.store_chunks.assert_awaited_once()

    async def test_execute_passes_tenant_id_as_the_real_keyword_argument(self):
        """Real, confirmed bug found live: tenant_id used to be bundled
        inside the metadata dict (store_chunks' 3rd positional arg)
        instead of passed as store_chunks' own real tenant_id keyword
        parameter, which then silently defaulted to None. Downstream,
        that None made every per-chunk Neo4j write's MATCH clause match
        zero rows (Cypher's null = null is never true), so store_chunks
        still reported success while creating nothing - and every real
        tenant-uploaded policy playbook silently never took effect,
        forever, for every tenant. This asserts the fix at the call
        boundary: tenant_id must be the real keyword argument, not
        merely present somewhere in whatever's passed as metadata."""
        agent = PolicyChunkingAgent()
        agent.storage_service.store_chunks = AsyncMock(return_value={"success": True, "chunks_stored": 1})

        context = AgentContext(
            input_data={
                "policy_text": SAMPLE_POLICY,
                "tenant_id": "tenant_a",
                "policy_name": "Test Liability Policy",
            },
            workflow_context=None,
        )

        await agent.execute(context)

        _, kwargs = agent.storage_service.store_chunks.call_args
        self.assertEqual(
            kwargs.get("tenant_id"), "tenant_a",
            f"tenant_id must be the real keyword argument, got kwargs={kwargs}",
        )

    async def test_execute_reports_error_status_on_missing_required_field(self):
        agent = PolicyChunkingAgent()

        # No tenant_id in input_data.
        context = AgentContext(input_data={"policy_text": SAMPLE_POLICY}, workflow_context=None)

        result = await agent.execute(context)

        self.assertEqual(result.status, "error")
        self.assertIn("error", result.data)


class PolicyChunkingAgentRunningEventLoopRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Regression test for a real production bug, confirmed live: POST
    /api/policies/upload failed 100% of the time. PolicyChunkingAgent.execute
    used to be a sync `def` that called asyncio.run(self.storage_service.
    store_chunks(...)) internally - but its only real caller,
    PolicyWorkflowOrchestrator.process_policy_document, is itself async
    and already has a running event loop by the time it calls execute(),
    so asyncio.run() raised RuntimeError every time, silently swallowed
    into a generic "Policy processing failed" response. The tests above
    never caught this because IsolatedAsyncioTestCase's setup previously
    ran execute() synchronously with no event loop running - this test
    specifically drives execute() through PolicyWorkflowOrchestrator's
    real, already-running event loop, the actual failure mode."""

    async def test_orchestrator_runs_policy_chunking_agent_without_raising(self):
        from backend.agents.policy_workflow_orchestrator import PolicyWorkflowOrchestrator

        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            orchestrator = PolicyWorkflowOrchestrator()

        chunking_agent = orchestrator.registry.get_agent("policy_chunking")
        chunking_agent.storage_service.store_chunks = AsyncMock(
            return_value={"chunks_stored": 3}
        )

        # PolicyExtractionAgent.execute constructs its own local
        # ChunkingStorageService() rather than storing one on self, so it
        # must be patched at the class level, not via the instance.
        get_chunks_patch = patch(
            "backend.infrastructure.chunking.storage_service.ChunkingStorageService.get_chunks",
            AsyncMock(return_value=[]),
        )
        get_chunks_mock = get_chunks_patch.start()
        self.addCleanup(get_chunks_patch.stop)

        # This call is already inside this test's running event loop
        # (IsolatedAsyncioTestCase) - exactly like the real FastAPI route -
        # so it reproduces the RuntimeError pre-fix, and must succeed post-fix.
        result = await orchestrator.process_policy_document(
            {
                "policy_text": SAMPLE_POLICY,
                "tenant_id": "tenant_a",
                "policy_name": "Test Liability Policy",
            }
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["steps"][0]["agent_id"], "policy_chunking")
        self.assertEqual(result["steps"][0]["status"], "success")
        self.assertEqual(result["steps"][1]["agent_id"], "policy_extraction")
        self.assertEqual(result["steps"][1]["status"], "success")
        chunking_agent.storage_service.store_chunks.assert_awaited_once()
        get_chunks_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
