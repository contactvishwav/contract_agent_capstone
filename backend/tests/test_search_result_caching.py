"""
Closes a real gap found while verifying the README's "cache LLM responses
and GraphRAG results" claim: LLM responses were already cached (P3 item
20), but the actual GraphRAG retrieval path - the P3 item 16 native vector
index (db.index.vector.queryNodes) - had zero caching anywhere, in either
of its two call sites:

1. enhanced_contract_search_tool.py's get_contracts_multi_level (the
   EnhancedContractSearchTool entry point, all search levels).
2. chunk_embedding_service.py's ChunkEmbeddingService.search_similar_chunks.

Both now cache via the same Redis infra already used for precedent_clause/
deviation_analysis/jurisdiction_analysis (shared/cache/redis_cache.py),
keyed on a hash of the query text + tenant_id + filters (not the raw
embeddings service object, which isn't meaningful to hash and isn't
JSON-serializable).

backend/tests/conftest.py forces the cache singleton onto its deterministic
InMemoryCache fallback for the whole suite, so these tests exercise real
cache.get/cache.set round-trips, not a mock - a call-count assertion on the
underlying graph/embedding-service fake is what actually proves a second
identical call took the cache path instead of re-querying.
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.infrastructure.encryption import field_encryptor


def _async_return(value):
    async def _coro():
        return value
    return _coro()


def _search_tool_module():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        from backend.shared.utils import enhanced_contract_search_tool
    return enhanced_contract_search_tool


class CountingFakeGraph:
    def __init__(self, response):
        self._response = response
        self.call_count = 0

    def query(self, cypher, params=None):
        self.call_count += 1
        return self._response


class VectorSearchCachingTests(unittest.TestCase):
    """get_contracts_multi_level - the document/section/clause/relationship/
    chunk-level entry point behind EnhancedContractSearchTool, all of which
    ultimately CALL db.index.vector.queryNodes."""

    def _fake_embeddings(self):
        fake = MagicMock()
        fake.embed_query.return_value = [0.1, 0.2, 0.3]
        return fake

    def test_second_identical_call_does_not_requery_the_graph(self):
        tool = _search_tool_module()
        fake_graph = CountingFakeGraph(response=[{"total_count": 1, "contracts": [{"file_id": "c1"}]}])

        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", True), \
             patch.object(tool, "graph", fake_graph):
            first = tool.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_cache_1",
                summary_search="a distinctive liability clause query",
            )
            second = tool.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_cache_1",
                summary_search="a distinctive liability clause query",
            )

        self.assertEqual(fake_graph.call_count, 1, "Second identical call should be served from cache")
        self.assertEqual(first, second)

    def test_different_tenant_id_is_a_cache_miss(self):
        """The cache key must include tenant_id - otherwise one tenant's
        search results could leak into another tenant's identical-looking
        query, a real cross-tenant confidentiality risk for a caching layer
        added on top of an already tenant-scoped search."""
        tool = _search_tool_module()
        fake_graph = CountingFakeGraph(response=[{"total_count": 1, "contracts": [{"file_id": "c1"}]}])

        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", True), \
             patch.object(tool, "graph", fake_graph):
            tool.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_cache_2a",
                summary_search="another distinctive query",
            )
            tool.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_cache_2b",
                summary_search="another distinctive query",
            )

        self.assertEqual(fake_graph.call_count, 2, "Different tenant_id must not share a cache entry")

    def test_cache_disabled_always_requeries(self):
        tool = _search_tool_module()
        fake_graph = CountingFakeGraph(response=[{"total_count": 0, "contracts": []}])

        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False), \
             patch.object(tool, "graph", fake_graph):
            tool.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_cache_3",
                summary_search="cache disabled query",
            )
            tool.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_cache_3",
                summary_search="cache disabled query",
            )

        self.assertEqual(fake_graph.call_count, 2, "CACHE_ENABLED=False must bypass caching entirely")


class ChunkEmbeddingSearchCachingTests(unittest.IsolatedAsyncioTestCase):
    """ChunkEmbeddingService.search_similar_chunks - the other real
    vector-index (GraphRAG) retrieval call site."""

    def _service(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"), \
             patch("backend.shared.utils.gemini_embedding_service.GeminiEmbeddingService"):
            from backend.infrastructure.chunking.chunk_embedding_service import ChunkEmbeddingService
        return ChunkEmbeddingService()

    async def test_second_identical_call_does_not_reembed_or_requery(self):
        service = self._service()
        service.embedding_service = MagicMock()
        service.embedding_service.generate_embedding = MagicMock(
            side_effect=lambda *_: _async_return([0.1, 0.2])
        )
        fake_graph = CountingFakeGraph(response=[{
            "chunk_id": "c1", "content": field_encryptor.encrypt("real chunk text, encrypted at rest"),
            "chunk_type": "sentence", "start_position": 0, "end_position": 10, "similarity": 0.9,
        }])
        service.graph = fake_graph

        with patch("backend.infrastructure.chunking.chunk_embedding_service.Phase3Config.CACHE_ENABLED", True):
            first = await service.search_similar_chunks("a distinctive chunk query", "tenant_cache_x", limit=5)
            second = await service.search_similar_chunks("a distinctive chunk query", "tenant_cache_x", limit=5)

        self.assertEqual(fake_graph.call_count, 1, "Second identical call should be served from cache")
        self.assertEqual(service.embedding_service.generate_embedding.call_count, 1,
                          "A cache hit must skip re-embedding the query text too")
        self.assertEqual(first, second)

    async def test_different_query_text_is_a_cache_miss(self):
        service = self._service()
        service.embedding_service = MagicMock()
        service.embedding_service.generate_embedding = MagicMock(
            side_effect=lambda *_: _async_return([0.1, 0.2])
        )
        fake_graph = CountingFakeGraph(response=[])
        service.graph = fake_graph

        with patch("backend.infrastructure.chunking.chunk_embedding_service.Phase3Config.CACHE_ENABLED", True):
            await service.search_similar_chunks("first distinct query", "tenant_cache_y")
            await service.search_similar_chunks("a completely different query", "tenant_cache_y")

        self.assertEqual(fake_graph.call_count, 2)

    async def test_different_tenant_id_is_a_cache_miss(self):
        service = self._service()
        service.embedding_service = MagicMock()
        service.embedding_service.generate_embedding = MagicMock(
            side_effect=lambda *_: _async_return([0.1, 0.2])
        )
        fake_graph = CountingFakeGraph(response=[])
        service.graph = fake_graph

        with patch("backend.infrastructure.chunking.chunk_embedding_service.Phase3Config.CACHE_ENABLED", True):
            await service.search_similar_chunks("same query text", "tenant_cache_z1")
            await service.search_similar_chunks("same query text", "tenant_cache_z2")

        self.assertEqual(fake_graph.call_count, 2,
                          "Different tenant_id must not share a cache entry, even for identical query text")


class TenantScopedChunkGraph:
    """Simulates Document {tenant_id}-[:HAS_CHUNK]->Chunk with real
    per-tenant filtering, for search_similar_chunks's
    'CALL db.index.vector.queryNodes(...) YIELD node AS c, score AS
    similarity MATCH (d:Document {tenant_id: $tenant_id})-[:HAS_CHUNK]->(c)'
    query shape - unlike CountingFakeGraph (a fixed response regardless of
    params), this actually enforces tenant_id the way a real Neo4j graph
    would, so a cross-tenant test against it is a genuine isolation proof,
    not just a query-string inspection."""

    def __init__(self):
        self.chunks = []

    def add_chunk(self, tenant_id: str, chunk_id: str, content: str):
        """content is the plaintext - stored encrypted, matching real ingestion."""
        self.chunks.append({"tenant_id": tenant_id, "chunk_id": chunk_id, "content": field_encryptor.encrypt(content)})

    def query(self, cypher: str, params: dict = None):
        params = params or {}
        if "db.index.vector.queryNodes" in cypher and "HAS_CHUNK" in cypher:
            tenant_id = params.get("tenant_id")
            return [
                {
                    "chunk_id": c["chunk_id"], "content": c["content"], "chunk_type": "sentence",
                    "start_position": 0, "end_position": len(c["content"]), "similarity": 0.95,
                }
                for c in self.chunks if c["tenant_id"] == tenant_id
            ]
        return []


class ChunkSearchCrossTenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    """Regression test matching the live-verified isolation pattern used
    elsewhere in this engagement (e.g. the E2E walkthrough's real 404 on a
    cross-tenant contract read): tenant A's chunks must not be returned for
    tenant B's identical query, now that search_similar_chunks is actually
    tenant-scoped (previously it took no tenant_id at all - found while
    adding caching to this path)."""

    def _service(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"), \
             patch("backend.shared.utils.gemini_embedding_service.GeminiEmbeddingService"):
            from backend.infrastructure.chunking.chunk_embedding_service import ChunkEmbeddingService
        return ChunkEmbeddingService()

    async def test_tenant_a_chunk_not_returned_for_tenant_b_identical_query(self):
        service = self._service()
        service.embedding_service = MagicMock()
        service.embedding_service.generate_embedding = MagicMock(
            side_effect=lambda *_: _async_return([0.1, 0.2])
        )
        graph = TenantScopedChunkGraph()
        graph.add_chunk("tenant_a", "chunk_a1", "Either party may terminate with 30 days notice.")
        service.graph = graph

        with patch("backend.infrastructure.chunking.chunk_embedding_service.Phase3Config.CACHE_ENABLED", False):
            tenant_b_results = await service.search_similar_chunks(
                "termination notice period", "tenant_b"
            )
            # Positive control: the same query, as tenant_a, must actually
            # find the chunk - proves the empty result above is real
            # isolation, not the fake graph/test being broken.
            tenant_a_results = await service.search_similar_chunks(
                "termination notice period", "tenant_a"
            )

        self.assertEqual(tenant_b_results, [])
        self.assertEqual(len(tenant_a_results), 1)
        self.assertEqual(tenant_a_results[0]["chunk_id"], "chunk_a1")


if __name__ == "__main__":
    unittest.main()
