"""
Tests for re-ranking extended to the remaining search levels/paths,
beyond the original DocumentSearchStrategy/ClauseSearchStrategy build
covered by test_search_reranking_integration.py:

  1. search_strategies.py's SectionSearchStrategy/RelationshipSearchStrategy
     (the REST API path, backend/api/enhanced_contract_search.py).
  2. enhanced_contract_search_tool.py's get_contracts_multi_level - the
     separate, LangChain-tool-facing implementation used by
     EnhancedContractSearchTool/Contract Chat (docs/CAPSTONE_SUMMARY.md
     §18/§19's "found via passing" duplication note; kept as a genuinely
     separate implementation rather than consolidated - see that section
     for the reasoning: real, independent behavioral divergence already
     exists between the two paths - different similarity thresholds
     (0.3 vs 0.8), different capability sets (CHUNK/ALL search levels,
     governing_law/monetary_value/cypher_aggregation filters) - so this
     extension preserves each path's existing behavior rather than
     merging them).

Same structure as test_search_reranking_integration.py: RerankerService
itself is unit-tested in test_reranker_service.py; these tests prove the
wiring - order changes correctly, the flag gates old vs. new behavior,
tenant isolation survives, cache keys are correct, and (found live while
wiring this) a real pre-existing ciphertext-as-clause-content bug in
enhanced_contract_search_tool.py's _search_clauses, the same class
already fixed in search_strategies.py's ClauseSearchStrategy.
"""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import backend.shared.utils.search_strategies as search_strategies
import backend.shared.utils.enhanced_contract_search_tool as enhanced_tool
from backend.agents.reranker_service import _RerankResponse, _RerankedItem, RerankOutcome
from backend.domain.search_entities import SearchParams, SearchLevel
from backend.infrastructure.encryption import field_encryptor
from backend.shared.config.phase3_config import Phase3Config
from backend.shared.reliability.circuit_breaker import CircuitBreaker


def _wrap(response):
    return {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}),
        "parsed": response,
        "parsing_error": None,
    }


def _make_fake_llm(response):
    fake_llm = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = _wrap(response)
    fake_llm.with_structured_output.return_value = structured
    return fake_llm, structured


@contextmanager
def _reranker_fallback_llm(fake_llm=None, raises=None):
    """See test_search_reranking_integration.py's identical helper -
    RerankerService's real construction changed from
    `RerankerService(get_reranker_llm())` to `RerankerService(
    use_fallback=True)` (real multi-provider fallback, backend/agents/
    llm_fallback_service.py); this injects a fake single-provider chain
    in its place so these tests keep proving the same real behavior
    against the new construction path. Yields the factory MagicMock for
    assert_called_once()/assert_not_called(), matching the old
    get_reranker_llm() mock's usage."""
    def _side_effect(timeout_seconds):
        if raises is not None:
            raise raises
        return fake_llm

    factory_mock = MagicMock(side_effect=_side_effect)
    breaker = CircuitBreaker("test_reranker_fallback_chain", failure_threshold=5, recovery_timeout_seconds=30.0)
    chain = [{"name": "gemini", "model": "gemini-2.5-flash", "factory": factory_mock, "breaker": breaker}]
    with patch("backend.agents.llm_fallback_service._RERANKER_CHAIN", chain):
        yield factory_mock


class FakeEmbeddingService:
    def embed_query(self, text):
        return [0.1] * 1536


# --- Part 1: search_strategies.py's Section/Relationship strategies -----

class FakeSectionRelationshipGraph:
    """Tags rows by the tenant_id the query actually carried, same proof
    shape as test_search_reranking_integration.py's FakeTwoTenantGraph."""

    def query(self, cypher, params=None):
        params = params or {}
        tenant_id = params.get("tenant_id")
        if "sections:" in cypher:
            sections = [
                {"contract_id": f"{tenant_id}_c{i}", "section_type": "termination",
                 "content": f"{tenant_id} section content {i}", "order": i}
                for i in range(2)
            ] if tenant_id else []
            return [{"result": {"total_count": len(sections), "sections": sections}}]
        relationships = [
            {"contract_id": f"{tenant_id}_c{i}", "party_name": f"Party{i}",
             "role": "vendor", "context": f"{tenant_id} relationship context {i}"}
            for i in range(2)
        ] if tenant_id else []
        return [{"result": {"total_count": len(relationships), "relationships": relationships}}]


class SectionRelationshipRerankingTests(unittest.TestCase):
    def setUp(self):
        self.fake_graph = FakeSectionRelationshipGraph()
        self._graph_patch = patch.object(search_strategies, "graph", self.fake_graph)
        self._embedding_patch = patch.object(search_strategies, "embedding", FakeEmbeddingService())
        self._graph_patch.start()
        self._embedding_patch.start()
        self.addCleanup(self._graph_patch.stop)
        self.addCleanup(self._embedding_patch.stop)

    def test_section_search_reranking_changes_order(self):
        # 2 candidates in original order [c0, c1]; the fake ranking scores
        # index 1 higher, so a real reorder (not a no-op) must land [c1, c0].
        response = _RerankResponse(rankings=[
            _RerankedItem(index=1, relevance_score=0.95),
            _RerankedItem(index=0, relevance_score=0.2),
        ])
        fake_llm, structured = _make_fake_llm(response)
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm(fake_llm):
            result = search_strategies.SectionSearchStrategy().execute(
                SearchParams(search_level=SearchLevel.SECTION, tenant_id="tenant_a", query="termination clause")
            )
        self.assertEqual(result.items[0]["contract_id"], "tenant_a_c1")
        self.assertEqual(result.items[1]["contract_id"], "tenant_a_c0")
        self.assertTrue(result.search_metadata["reranking"]["applied"])
        structured.invoke.assert_called_once()

    def test_relationship_search_reranking_changes_order(self):
        response = _RerankResponse(rankings=[
            _RerankedItem(index=1, relevance_score=0.95),
            _RerankedItem(index=0, relevance_score=0.2),
        ])
        fake_llm, structured = _make_fake_llm(response)
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm(fake_llm):
            result = search_strategies.RelationshipSearchStrategy().execute(
                SearchParams(search_level=SearchLevel.RELATIONSHIP, tenant_id="tenant_a", query="vendor relationship")
            )
        self.assertEqual(result.items[0]["contract_id"], "tenant_a_c1")
        self.assertEqual(result.items[1]["contract_id"], "tenant_a_c0")
        structured.invoke.assert_called_once()

    def test_flag_off_section_search_unchanged_no_llm_call(self):
        with patch.object(Phase3Config, "RERANKING_ENABLED", False), \
             _reranker_fallback_llm() as factory_mock:
            result = search_strategies.SectionSearchStrategy().execute(
                SearchParams(search_level=SearchLevel.SECTION, tenant_id="tenant_a", query="termination")
            )
        factory_mock.assert_not_called()
        self.assertNotIn("reranking", result.search_metadata)

    def test_flag_on_no_query_relationship_search_does_not_engage(self):
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm() as factory_mock:
            result = search_strategies.RelationshipSearchStrategy().execute(
                SearchParams(search_level=SearchLevel.RELATIONSHIP, tenant_id="tenant_a", query=None)
            )
        factory_mock.assert_not_called()
        self.assertNotIn("reranking", result.search_metadata)

    def test_graceful_degradation_on_reranker_failure_section_search(self):
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm(raises=RuntimeError("boom")):
            result = search_strategies.SectionSearchStrategy().execute(
                SearchParams(search_level=SearchLevel.SECTION, tenant_id="tenant_a", query="termination")
            )
        # The provider construction blowing up must not crash the search -
        # falls back to unranked results just like RerankerService's own
        # documented graceful-degradation contract.
        self.assertEqual(len(result.items), 2)

    def test_tenant_isolation_reranker_only_sees_own_tenants_candidates(self):
        seen = []

        def fake_rerank(self, query, candidates, text_key, top_k):
            seen.extend(c["contract_id"] for c in candidates)
            return RerankOutcome(results=candidates[:top_k], reranked=True)

        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             patch("backend.agents.reranker_service.RerankerService.rerank", fake_rerank):
            search_strategies.SectionSearchStrategy().execute(
                SearchParams(search_level=SearchLevel.SECTION, tenant_id="tenant_a", query="termination")
            )
        self.assertTrue(all(cid.startswith("tenant_a_") for cid in seen))
        self.assertTrue(len(seen) > 0)


# --- Part 2: enhanced_contract_search_tool.py's get_contracts_multi_level -

class FakeMultiLevelGraph:
    """One fake graph serving all 4 wired _search_* functions in
    enhanced_contract_search_tool.py, dispatching on a distinctive
    substring in the Cypher (same technique the module's own docstrings
    use to distinguish RETURN clauses) - and tagging rows by tenant_id for
    the isolation proof, matching FakeSectionRelationshipGraph above."""

    ENCRYPTED_CLAUSE_CONTENT = "clause content for"

    def query(self, cypher, params=None):
        params = params or {}
        tenant_id = params.get("tenant_id")
        if "contracts:" in cypher:
            contracts = [
                {"file_id": f"{tenant_id}_c{i}", "summary": f"{tenant_id} contract summary {i}",
                 "contract_type": "MSA", "effective_date": None, "end_date": None, "parties": []}
                for i in range(2)
            ] if tenant_id else []
            return [{"result": {"total_count": len(contracts), "contracts": contracts}}]
        if "sections:" in cypher:
            sections = [
                {"contract_id": f"{tenant_id}_c{i}", "section_type": "termination",
                 "content": f"{tenant_id} section {i}", "order": i}
                for i in range(2)
            ] if tenant_id else []
            return [{"result": {"total_count": len(sections), "sections": sections}}]
        if "clauses:" in cypher:
            # Real encrypted-at-rest ciphertext, exactly like production -
            # proves _search_clauses genuinely decrypts before returning/
            # reranking, not a plaintext stand-in that could pass either way.
            clauses = [
                {"contract_id": f"{tenant_id}_c{i}", "clause_type": "liability",
                 "content": field_encryptor.encrypt(f"{tenant_id} {self.ENCRYPTED_CLAUSE_CONTENT} {i}"),
                 "confidence": 0.9, "start_position": 0, "end_position": 10}
                for i in range(2)
            ] if tenant_id else []
            return [{"result": {"total_count": len(clauses), "clauses": clauses}}]
        if "relationships:" in cypher:
            relationships = [
                {"contract_id": f"{tenant_id}_c{i}", "party_name": f"Party{i}",
                 "role": "vendor", "context": f"{tenant_id} relationship {i}"}
                for i in range(2)
            ] if tenant_id else []
            return [{"result": {"total_count": len(relationships), "relationships": relationships}}]
        raise AssertionError(f"unexpected cypher in FakeMultiLevelGraph: {cypher[:80]}")


class EnhancedToolRerankingTests(unittest.TestCase):
    def setUp(self):
        self.fake_graph = FakeMultiLevelGraph()
        self._graph_patch = patch.object(enhanced_tool, "graph", self.fake_graph)
        self._graph_patch.start()
        self.addCleanup(self._graph_patch.stop)
        self.embeddings = FakeEmbeddingService()

    def test_document_search_reranking_changes_order(self):
        response = _RerankResponse(rankings=[
            _RerankedItem(index=1, relevance_score=0.95),
            _RerankedItem(index=0, relevance_score=0.2),
        ])
        fake_llm, structured = _make_fake_llm(response)
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm(fake_llm):
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", enhanced_tool.SearchLevel.DOCUMENT, summary_search="msa contract"
            )
        contracts = result[0]["result"]["contracts"]
        self.assertEqual(contracts[0]["file_id"], "tenant_a_c1")
        self.assertEqual(contracts[1]["file_id"], "tenant_a_c0")
        self.assertTrue(result[0]["result"]["reranking"]["applied"])
        structured.invoke.assert_called_once()

    def test_string_search_level_does_not_crash_regression(self):
        """Regression test for a real bug found live while verifying this
        session's re-ranking work through Contract Chat: EnhancedContractSearchTool
        is tenant-scoped, so contract_chat_agent.py's execute_tools calls
        tool._run(**args) directly with the LLM's raw tool-call args,
        bypassing args_schema's str->SearchLevel coercion entirely - so
        search_level arrives here as a plain str ("document"), not the
        enum. _multi_level_search_cache_key's search_level.value crashed
        with AttributeError on this in real production/local traffic
        whenever the LLM specified an explicit search_level - reproduced
        directly here without any LLM involved."""
        with patch.object(Phase3Config, "RERANKING_ENABLED", False):
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", "document", summary_search="msa"
            )
        self.assertEqual(len(result[0]["result"]["contracts"]), 2)

        with patch.object(Phase3Config, "RERANKING_ENABLED", False):
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", "relationship", summary_search="vendor"
            )
        self.assertEqual(len(result[0]["result"]["relationships"]), 2)

    def test_flag_off_document_search_unchanged_no_reranking_key(self):
        with patch.object(Phase3Config, "RERANKING_ENABLED", False), \
             _reranker_fallback_llm() as factory_mock:
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", enhanced_tool.SearchLevel.DOCUMENT, summary_search="msa"
            )
        factory_mock.assert_not_called()
        self.assertNotIn("reranking", result[0]["result"])

    def test_flag_on_no_query_does_not_engage(self):
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm() as factory_mock:
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", enhanced_tool.SearchLevel.SECTION, summary_search=None
            )
        factory_mock.assert_not_called()
        self.assertNotIn("reranking", result[0]["result"])

    def test_graceful_degradation_on_reranker_failure(self):
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm(raises=RuntimeError("boom")):
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", enhanced_tool.SearchLevel.RELATIONSHIP, summary_search="vendor"
            )
        self.assertEqual(len(result[0]["result"]["relationships"]), 2)

    def test_tenant_isolation_reranker_only_sees_own_tenants_candidates(self):
        seen = []

        def fake_rerank(self, query, candidates, text_key, top_k):
            seen.extend(c["contract_id"] for c in candidates)
            return RerankOutcome(results=candidates[:top_k], reranked=True)

        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             patch("backend.agents.reranker_service.RerankerService.rerank", fake_rerank):
            enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", enhanced_tool.SearchLevel.SECTION, summary_search="termination"
            )
        self.assertTrue(all(cid.startswith("tenant_a_") for cid in seen))
        self.assertTrue(len(seen) > 0)

    def test_clause_search_decrypts_before_reranking_regression(self):
        """Regression test for the real bug found while wiring this: cl.content
        is encrypted at rest and this path never decrypted it - reranking
        would have scored ciphertext. Proves the reranker's candidates
        genuinely carry decrypted plaintext, and the final returned content
        is plaintext too, not the ciphertext FakeMultiLevelGraph seeded."""
        seen_texts = []

        def fake_rerank(self, query, candidates, text_key, top_k):
            seen_texts.extend(c[text_key] for c in candidates)
            return RerankOutcome(results=candidates[:top_k], reranked=True)

        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             patch("backend.agents.reranker_service.RerankerService.rerank", fake_rerank):
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", enhanced_tool.SearchLevel.CLAUSE, summary_search="liability"
            )

        self.assertTrue(seen_texts, "reranker should have received clause candidates")
        for text in seen_texts:
            self.assertIn(FakeMultiLevelGraph.ENCRYPTED_CLAUSE_CONTENT, text)

        clauses = result[0]["result"]["clauses"]
        for clause in clauses:
            self.assertIn(FakeMultiLevelGraph.ENCRYPTED_CLAUSE_CONTENT, clause["content"])

    def test_clause_search_decrypts_even_when_reranking_disabled(self):
        """The decryption fix is not conditional on the reranking flag -
        this path must never leak ciphertext, flag on or off."""
        with patch.object(Phase3Config, "RERANKING_ENABLED", False):
            result = enhanced_tool.get_contracts_multi_level(
                self.embeddings, "tenant_a", enhanced_tool.SearchLevel.CLAUSE, summary_search="liability"
            )
        clauses = result[0]["result"]["clauses"]
        self.assertTrue(clauses)
        for clause in clauses:
            self.assertIn(FakeMultiLevelGraph.ENCRYPTED_CLAUSE_CONTENT, clause["content"])


class MultiLevelCacheKeyRerankingFlagTests(unittest.TestCase):
    """The cache key must include RERANKING_ENABLED's state - otherwise
    flipping the flag would serve a stale cached page (wrong page size,
    missing/stale reranking metadata) from before the flip, exactly the
    requirement already applied to the REST path's cache key (§18)."""

    def test_cache_key_differs_when_only_reranking_flag_differs(self):
        common_args = dict(
            tenant_id="tenant_a", search_level=enhanced_tool.SearchLevel.DOCUMENT,
            clause_types=None, section_types=None, min_effective_date=None, max_effective_date=None,
            min_end_date=None, max_end_date=None, contract_type=None, parties=None,
            summary_search="msa", active=None, cypher_aggregation=None,
            monetary_value=None, governing_law=None,
        )
        with patch.object(Phase3Config, "RERANKING_ENABLED", False):
            key_off = enhanced_tool._multi_level_search_cache_key(**common_args)
        with patch.object(Phase3Config, "RERANKING_ENABLED", True):
            key_on = enhanced_tool._multi_level_search_cache_key(**common_args)
        self.assertNotEqual(key_off, key_on)


if __name__ == "__main__":
    unittest.main()
