"""
Integration-level tests for re-ranking wired into the real search path
(search_strategies.py's DocumentSearchStrategy/ClauseSearchStrategy,
gated by Phase3Config.RERANKING_ENABLED, cached via
EnhancedSearchService.search()) - as opposed to test_reranker_service.py's
unit tests of RerankerService in isolation.

Covers: tenant isolation is preserved through the re-ranking stage, the
feature flag genuinely gates old vs. new behavior, and repeated identical
queries hit the cache instead of re-invoking the re-ranker.
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import backend.shared.utils.search_strategies as search_strategies
from backend.application.services.enhanced_search_service import EnhancedSearchService
from backend.domain.search_entities import SearchParams, SearchLevel
from backend.agents.reranker_service import _RerankResponse, _RerankedItem
from backend.shared.cache.redis_cache import cache
from backend.shared.config.phase3_config import Phase3Config
from backend.shared.reliability.circuit_breaker import CircuitBreaker


def _wrap(response):
    return {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}),
        "parsed": response,
        "parsing_error": None,
    }


@contextmanager
def _reranker_fallback_llm(fake_llm=None, raises=None):
    """RerankerService's real construction changed from
    `RerankerService(get_reranker_llm())` to `RerankerService(
    use_fallback=True)` (real multi-provider fallback, backend/agents/
    llm_fallback_service.py) - this replaces the old get_reranker_llm()-
    patching convention with an equivalent single-provider fake chain, so
    these tests keep proving the same real behavior against the new
    construction path. fake_llm=None means "no provider configured"
    (old get_reranker_llm() returning None); raises simulates a
    construction-time failure (old get_reranker_llm() raising). Yields
    the factory MagicMock so callers can assert_called_once()/
    assert_not_called(), matching the old get_reranker_llm() mock's usage."""
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


class FakeTwoTenantGraph:
    """
    Returns contracts tagged by which tenant_id the query actually carried
    - a stand-in for real Neo4j's own WHERE c.tenant_id = $tenant_id
    filtering. If the application code ever passed the wrong tenant_id (or
    none), this would return the wrong tenant's data - the same shape of
    proof test_enhanced_search_tenant_isolation.py already uses.
    """

    def __init__(self):
        self.queries = []

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        tenant_id = params.get("tenant_id")
        # Simulate a real corpus where each tenant has 3 real contracts -
        # only ever returns the querying tenant's own contracts.
        contracts = [
            # relevance_score present (dynamic_retrieval.py's score-delta
            # filter drops anything without one).
            {"file_id": f"{tenant_id}_contract_{i}", "summary": f"{tenant_id}'s confidential contract {i}",
             "contract_type": "MSA", "effective_date": None, "end_date": None, "parties": [], "relevance_score": 0.9}
            for i in range(3)
        ] if tenant_id else []
        return [{"result": {"total_count": len(contracts), "contracts": contracts}}]


class RerankingTenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.fake_graph = FakeTwoTenantGraph()
        self._graph_patch = patch.object(search_strategies, "graph", self.fake_graph)
        self._embedding_patch = patch.object(search_strategies, "embedding", FakeEmbeddingService())
        self._flag_patch = patch.object(Phase3Config, "RERANKING_ENABLED", True)
        self._graph_patch.start()
        self._embedding_patch.start()
        self._flag_patch.start()
        self.addCleanup(self._graph_patch.stop)
        self.addCleanup(self._embedding_patch.stop)
        self.addCleanup(self._flag_patch.stop)

    def test_reranker_only_ever_receives_the_calling_tenants_own_candidates(self):
        seen_candidate_ids = []

        def fake_rerank(self, query, candidates, text_key, top_k):
            seen_candidate_ids.extend(c["file_id"] for c in candidates)
            from backend.agents.reranker_service import RerankOutcome
            return RerankOutcome(results=candidates[:top_k], reranked=True)

        with patch("backend.agents.reranker_service.RerankerService.rerank", fake_rerank):
            strategy = search_strategies.DocumentSearchStrategy()
            strategy.execute(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_a", query="msa"))

        self.assertTrue(all(cid.startswith("tenant_a_") for cid in seen_candidate_ids))
        self.assertTrue(len(seen_candidate_ids) > 0)

    def test_two_different_tenants_never_see_each_others_reranked_results(self):
        response = _RerankResponse(rankings=[_RerankedItem(index=0, relevance_score=0.9)])
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.return_value = _wrap(response)
        fake_llm.with_structured_output.return_value = structured

        with _reranker_fallback_llm(fake_llm):
            strategy = search_strategies.DocumentSearchStrategy()
            result_a = strategy.execute(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_a", query="msa"))
            result_b = strategy.execute(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_b", query="msa"))

        ids_a = {c["file_id"] for c in result_a.items}
        ids_b = {c["file_id"] for c in result_b.items}
        self.assertTrue(all(i.startswith("tenant_a_") for i in ids_a))
        self.assertTrue(all(i.startswith("tenant_b_") for i in ids_b))
        self.assertEqual(ids_a & ids_b, set())


class FeatureFlagGatesRerankingTests(unittest.TestCase):
    def setUp(self):
        self.fake_graph = FakeTwoTenantGraph()
        self._graph_patch = patch.object(search_strategies, "graph", self.fake_graph)
        self._embedding_patch = patch.object(search_strategies, "embedding", FakeEmbeddingService())
        self._graph_patch.start()
        self._embedding_patch.start()
        self.addCleanup(self._graph_patch.stop)
        self.addCleanup(self._embedding_patch.stop)

    def test_flag_off_old_behavior_unchanged_no_reranking_metadata_no_llm_call(self):
        with patch.object(Phase3Config, "RERANKING_ENABLED", False), \
             _reranker_fallback_llm() as factory_mock:
            strategy = search_strategies.DocumentSearchStrategy()
            result = strategy.execute(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_a", query="msa"))

        factory_mock.assert_not_called()
        self.assertNotIn("reranking", result.search_metadata)
        # Cypher page_size must still be the original top-10, not the wider pool
        cypher, params = self.fake_graph.queries[-1]
        self.assertEqual(params.get("page_size"), 10)

    def test_flag_on_new_behavior_engages_reranking_metadata_present(self):
        response = _RerankResponse(rankings=[_RerankedItem(index=0, relevance_score=0.9)])
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.return_value = _wrap(response)
        fake_llm.with_structured_output.return_value = structured

        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm(fake_llm):
            strategy = search_strategies.DocumentSearchStrategy()
            result = strategy.execute(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_a", query="msa"))

        self.assertIn("reranking", result.search_metadata)
        self.assertTrue(result.search_metadata["reranking"]["applied"])
        structured.invoke.assert_called_once()
        cypher, params = self.fake_graph.queries[-1]
        self.assertEqual(params.get("page_size"), 30, "wider RERANK_POOL_SIZE pool requested when reranking is on")

    def test_flag_on_but_no_query_does_not_engage_reranking(self):
        """Reranking has nothing to judge relevance against without a real
        query - a browse-all/filter-only request must not call the LLM."""
        with patch.object(Phase3Config, "RERANKING_ENABLED", True), \
             _reranker_fallback_llm() as factory_mock:
            strategy = search_strategies.DocumentSearchStrategy()
            result = strategy.execute(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_a", query=None))

        factory_mock.assert_not_called()
        self.assertNotIn("reranking", result.search_metadata)


class RerankedResultCachingTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client._cache.clear()
        self.addCleanup(cache.redis_client._cache.clear)
        self.fake_graph = FakeTwoTenantGraph()
        self._graph_patch = patch.object(search_strategies, "graph", self.fake_graph)
        self._embedding_patch = patch.object(search_strategies, "embedding", FakeEmbeddingService())
        self._flag_patch = patch.object(Phase3Config, "RERANKING_ENABLED", True)
        self._cache_patch = patch.object(Phase3Config, "CACHE_ENABLED", True)
        self._graph_patch.start()
        self._embedding_patch.start()
        self._flag_patch.start()
        self._cache_patch.start()
        self.addCleanup(self._graph_patch.stop)
        self.addCleanup(self._embedding_patch.stop)
        self.addCleanup(self._flag_patch.stop)
        self.addCleanup(self._cache_patch.stop)

    def test_repeated_identical_query_hits_cache_reranker_called_once(self):
        response = _RerankResponse(rankings=[_RerankedItem(index=0, relevance_score=0.9)])
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.return_value = _wrap(response)
        fake_llm.with_structured_output.return_value = structured

        with _reranker_fallback_llm(fake_llm):
            service = EnhancedSearchService()
            params = SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_a", query="msa")
            first = service.search(params)
            second = service.search(params)

        structured.invoke.assert_called_once()
        self.assertEqual(first.items, second.items)
        self.assertEqual(
            [c["file_id"] for c in second.items], [c["file_id"] for c in first.items]
        )

    def test_different_tenant_same_query_is_a_cache_miss(self):
        """Real proof the cache key is tenant-scoped, not just query-scoped -
        two tenants asking the identical query text must not share a cache
        entry (which would leak one tenant's result set to another)."""
        response = _RerankResponse(rankings=[_RerankedItem(index=0, relevance_score=0.9)])
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.return_value = _wrap(response)
        fake_llm.with_structured_output.return_value = structured

        with _reranker_fallback_llm(fake_llm):
            service = EnhancedSearchService()
            service.search(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_a", query="msa"))
            service.search(SearchParams(search_level=SearchLevel.DOCUMENT, tenant_id="tenant_b", query="msa"))

        self.assertEqual(structured.invoke.call_count, 2, "different tenants must not share a cache entry")


if __name__ == "__main__":
    unittest.main()
