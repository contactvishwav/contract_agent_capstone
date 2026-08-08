"""
Follow-up to P3 item 21: Chunk.content/DocumentChunk.content was a third
copy of contract text (used for chunked retrieval/search) that remained
unencrypted and unredacted after the Contract.full_text/Clause.content work.

Confirmed live CONTAINS/substring() Cypher dependencies on this content
before proceeding (per instruction) - unlike full_text/Clause.content,
6 call sites across enhanced_contract_search_tool.py and storage_service.py
did in-Cypher CONTAINS matching and substring() snippet slicing directly on
the stored property, including in the *primary* (non-fallback) semantic
search path's snippet generation. Encrypting in place required moving both
operations into application code, operating on content decrypted via the
same field_encryptor/PIIEngine helpers already built for Contract/Clause -
not a copy-paste of that pattern, a real restructure of 6 query call sites.

This file exercises: write-path redact+encrypt (2 sites: Chunk via
ChunkingStorageService.store_chunks, DocumentChunk via ChunkStorageService.
store_chunks - ChunkEmbeddingService.store_chunk_embeddings no longer
writes content at all, see its own test below and test_chunk_embedding_
persistence.py for why), read-path decrypt (5 sites), the CONTAINS-fallback
still finding matches
against encrypted content post-decryption, and the substring()-snippet
replacement producing real decrypted text (not garbled/base64) in both the
primary semantic path and the fallback.
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.governance.pii_engine import PIIEngine
from backend.infrastructure.encryption import field_encryptor
from backend.shared.utils.vector_index_config import CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT

SSN_CHUNK_TEXT = "This chunk mentions an SSN 123-45-6789 among other contract language."
PLAIN_CHUNK_TEXT = "This chunk is a completely unremarkable piece of contract boilerplate text."


class FakeGraph:
    """Records every issued (cypher, params); returns [] by default.
    Subclass/override `query` per test to serve specific Cypher shapes."""

    def __init__(self):
        self.queries = []

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        return []


# ---------------------------------------------------------------------------
# storage_service.py (ChunkingStorageService: Chunk nodes)
# ---------------------------------------------------------------------------

def _storage_service():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        from backend.infrastructure.chunking.storage_service import ChunkingStorageService
    service = ChunkingStorageService()
    fake_graph = FakeGraph()
    service.graph = fake_graph
    service.chunk_embedding_service = MagicMock()
    return service, fake_graph


class ChunkingStorageServiceEncryptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_chunks_encrypts_content(self):
        service, fake_graph = _storage_service()

        await service.store_chunks("doc1", [{"content": SSN_CHUNK_TEXT, "chunk_index": 0}])

        create_calls = [(c, p) for c, p in fake_graph.queries if "CREATE (c:Chunk" in c]
        self.assertEqual(len(create_calls), 1)
        stored_content = create_calls[0][1]["content"]
        self.assertNotEqual(stored_content, SSN_CHUNK_TEXT)
        self.assertNotIn("123-45-6789", stored_content)

    async def test_get_chunks_decrypts_content(self):
        service, fake_graph = _storage_service()
        encrypted = field_encryptor.encrypt(SSN_CHUNK_TEXT)

        class ReadGraph(FakeGraph):
            def query(self, cypher, params=None):
                if "RETURN c.id as chunk_id" in cypher and "MATCH (d:Document {id:" in cypher:
                    return [{
                        "chunk_id": "doc1_chunk_0", "content": encrypted, "start_position": 0,
                        "end_position": 10, "chunk_type": "sentence", "size": 10, "chunk_index": 0,
                        "quality_score": 0.9, "has_overlap": False, "overlap_size": 0,
                        "embedding_ready": True, "parent_section": "", "clause_count": 0,
                    }]
                return super().query(cypher, params)

        service.graph = ReadGraph()
        result = await service.get_chunks("doc1")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], SSN_CHUNK_TEXT)

    async def test_text_search_chunks_finds_match_against_encrypted_content(self):
        """The key regression: a CONTAINS-style fallback search must still
        find the right chunk even though Chunk.content is now ciphertext -
        proving the fetch-then-decrypt-then-match-in-Python replacement
        actually works, not just that it doesn't crash."""
        service, _ = _storage_service()
        encrypted_match = field_encryptor.encrypt(SSN_CHUNK_TEXT)
        encrypted_other = field_encryptor.encrypt(PLAIN_CHUNK_TEXT)

        class SearchGraph(FakeGraph):
            def query(self, cypher, params=None):
                if "MATCH (c:Chunk)" in cypher or "MATCH (d:Document {id:" in cypher:
                    return [
                        {"chunk_id": "c1", "content": encrypted_other, "chunk_type": "sentence",
                         "quality_score": 0.9, "start_position": 0, "end_position": 10},
                        {"chunk_id": "c2", "content": encrypted_match, "chunk_type": "sentence",
                         "quality_score": 0.5, "start_position": 0, "end_position": 10},
                    ]
                return super().query(cypher, params)

        service.graph = SearchGraph()
        results = await service._text_search_chunks("SSN 123-45-6789")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "c2")
        self.assertEqual(results[0]["content"], SSN_CHUNK_TEXT)

    async def test_basic_text_search_matches_case_insensitively(self):
        service, _ = _storage_service()
        encrypted_match = field_encryptor.encrypt(SSN_CHUNK_TEXT)

        class SearchGraph(FakeGraph):
            def query(self, cypher, params=None):
                if "MATCH (c:Chunk)" in cypher:
                    return [{"chunk_id": "c1", "content": encrypted_match, "chunk_type": "sentence", "quality_score": 0.9}]
                return super().query(cypher, params)

        service.graph = SearchGraph()
        results = await service._basic_text_search("ssn 123-45-6789")  # lowercase query

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], SSN_CHUNK_TEXT)

    async def test_text_search_candidate_fetch_is_bounded(self):
        service, fake_graph = _storage_service()
        await service._text_search_chunks("anything")

        candidate_limit_params = [p.get("candidate_limit") for _, p in fake_graph.queries if "candidate_limit" in p]
        self.assertTrue(candidate_limit_params)
        self.assertEqual(candidate_limit_params[0], CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT)


# ---------------------------------------------------------------------------
# storage_service.py (ChunkStorageService: DocumentChunk nodes, sync wrapper)
# ---------------------------------------------------------------------------

class ChunkStorageServiceDocumentChunkEncryptionTests(unittest.TestCase):
    def test_store_chunks_encrypts_document_chunk_content(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.infrastructure.chunking.storage_service import ChunkStorageService
        service = ChunkStorageService()
        fake_graph = FakeGraph()
        service.graph = fake_graph

        service.store_chunks("contract1", [{"content": SSN_CHUNK_TEXT}])

        merge_calls = [(c, p) for c, p in fake_graph.queries if "MERGE (dc:DocumentChunk" in c]
        self.assertEqual(len(merge_calls), 1)
        stored_content = merge_calls[0][1]["content"]
        self.assertNotEqual(stored_content, SSN_CHUNK_TEXT)
        self.assertNotIn("123-45-6789", stored_content)


# ---------------------------------------------------------------------------
# chunk_embedding_service.py
# ---------------------------------------------------------------------------

def _chunk_embedding_service():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"), \
         patch("backend.shared.utils.gemini_embedding_service.GeminiEmbeddingService"):
        from backend.infrastructure.chunking.chunk_embedding_service import ChunkEmbeddingService, ChunkEmbedding
    service = ChunkEmbeddingService()
    fake_graph = FakeGraph()
    service.graph = fake_graph
    return service, fake_graph, ChunkEmbedding


class ChunkEmbeddingServiceEncryptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_chunk_embeddings_does_not_write_content_at_all(self):
        """Real, confirmed bug found live (see test_chunk_embedding_
        persistence.py): store_chunk_embeddings used to MATCH (d:Document)
        and CREATE a brand-new Chunk node - including its own copy of
        content - which ran before the Document even existed on the real
        pipeline, so the write silently never happened. Fixed by having
        this method only attach an embedding to a Chunk node that
        ChunkingStorageService.store_chunks already created (and already
        redacted+encrypted) - it must not write content at all anymore,
        so there is nothing here left to encrypt."""
        service, fake_graph, ChunkEmbedding = _chunk_embedding_service()
        chunk_embedding = ChunkEmbedding(
            chunk_id="doc1_chunk_0", document_id="doc1", embedding=[0.1],
            chunk_content=SSN_CHUNK_TEXT, chunk_metadata={},
        )

        await service.store_chunk_embeddings([chunk_embedding])

        self.assertEqual(len(fake_graph.queries), 1)
        cypher, params = fake_graph.queries[0]
        self.assertIn("MATCH (c:Chunk", cypher)
        self.assertNotIn("CREATE (c:Chunk", cypher)
        self.assertNotIn("content", params)

    async def test_search_similar_chunks_decrypts_content(self):
        service, _, _ = _chunk_embedding_service()
        service.embedding_service = MagicMock()
        service.embedding_service.generate_embedding = MagicMock(return_value=_async_return([0.1]))
        encrypted = field_encryptor.encrypt(SSN_CHUNK_TEXT)

        class ReadGraph(FakeGraph):
            def query(self, cypher, params=None):
                if "db.index.vector.queryNodes" in cypher:
                    return [{
                        "chunk_id": "c1", "content": encrypted, "chunk_type": "sentence",
                        "start_position": 0, "end_position": 10, "similarity": 0.9,
                    }]
                return super().query(cypher, params)

        service.graph = ReadGraph()
        results = await service.search_similar_chunks("some query", "tenant_1")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], SSN_CHUNK_TEXT)

    async def test_get_chunk_embeddings_by_document_decrypts_content(self):
        service, _, _ = _chunk_embedding_service()
        encrypted = field_encryptor.encrypt(SSN_CHUNK_TEXT)

        class ReadGraph(FakeGraph):
            def query(self, cypher, params=None):
                if "RETURN c.id as chunk_id, c.content as content, c.embedding" in cypher:
                    return [{
                        "chunk_id": "c1", "content": encrypted, "embedding": [0.1],
                        "chunk_type": "sentence", "start_position": 0, "end_position": 10,
                        "size": 10, "has_overlap": False, "overlap_size": 0, "quality_score": 0.9,
                    }]
                return super().query(cypher, params)

        service.graph = ReadGraph()
        results = await service.get_chunk_embeddings_by_document("doc1")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk_content, SSN_CHUNK_TEXT)


def _async_return(value):
    async def _coro(*args, **kwargs):
        return value
    return _coro()


# ---------------------------------------------------------------------------
# enhanced_contract_search_tool.py (_search_chunks)
# ---------------------------------------------------------------------------

def _search_tool_module():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        from backend.shared.utils import enhanced_contract_search_tool
    return enhanced_contract_search_tool


class ChunkSnippetTests(unittest.TestCase):
    def test_snippet_matches_old_substring_semantics(self):
        tool = _search_tool_module()
        long_text = "x" * 300
        self.assertEqual(tool._chunk_snippet(long_text), long_text[:200] + "...")


class SearchChunksEncryptionTests(unittest.TestCase):
    def test_semantic_search_returns_decrypted_snippet_not_ciphertext(self):
        """Primary (non-fallback) path regression guard: the vector-index
        search's own content preview must be real text, not a ciphertext
        fragment - this call site used substring(c.content, ...) directly
        in Cypher before, which would have returned garbled base64."""
        tool = _search_tool_module()
        encrypted = field_encryptor.encrypt(SSN_CHUNK_TEXT)

        fake_graph = FakeGraph()

        def query(cypher, params=None):
            if "db.index.vector.queryNodes" in cypher:
                return [{
                    "document_id": "doc1", "chunk_type": "sentence", "content": encrypted,
                    "chunk_index": 0, "quality_score": 0.9, "similarity_score": 0.95,
                }]
            return []
        fake_graph.query = query

        fake_embeddings = MagicMock()
        fake_embeddings.embed_query.return_value = [0.1]

        with patch.object(tool, "graph", fake_graph):
            output = tool._search_chunks(fake_embeddings, "tenant_1", "liability clause", [], {})

        chunks = output[0]["result"]["chunks"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["search_type"], "semantic")
        self.assertEqual(chunks[0]["content"], SSN_CHUNK_TEXT[:200] + "...")
        self.assertNotIn("123-45-6789", encrypted)  # sanity: ciphertext really doesn't contain plaintext

    def test_fallback_text_search_finds_match_against_encrypted_content(self):
        """The key regression: when semantic search returns nothing, the
        CONTAINS-based fallback must still find the right chunk in
        encrypted Chunk/DocumentChunk content."""
        tool = _search_tool_module()
        encrypted_match = field_encryptor.encrypt(SSN_CHUNK_TEXT)
        encrypted_other = field_encryptor.encrypt(PLAIN_CHUNK_TEXT)

        fake_graph = FakeGraph()

        def query(cypher, params=None):
            if "db.index.vector.queryNodes" in cypher:
                return []  # semantic search: no results, forces fallback
            if "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)" in cypher:
                return [
                    {"document_id": "doc1", "chunk_type": "sentence", "content": encrypted_other,
                     "chunk_index": 1, "quality_score": 0.9},
                    {"document_id": "doc1", "chunk_type": "sentence", "content": encrypted_match,
                     "chunk_index": 0, "quality_score": 0.5},
                ]
            if "MATCH (c:Contract)-[:CONTAINS_CHUNK]->(dc:DocumentChunk)" in cypher:
                return []
            return []
        fake_graph.query = query

        fake_embeddings = MagicMock()
        fake_embeddings.embed_query.return_value = [0.1]

        with patch.object(tool, "graph", fake_graph):
            output = tool._search_chunks(fake_embeddings, "tenant_1", "SSN 123-45-6789", [], {})

        new_chunks_result = output[0]["result"]
        self.assertEqual(new_chunks_result["total_count"], 1)
        self.assertEqual(len(new_chunks_result["chunks"]), 1)
        self.assertEqual(new_chunks_result["chunks"][0]["content"], SSN_CHUNK_TEXT[:200] + "...")
        self.assertEqual(new_chunks_result["chunks"][0]["search_type"], "text_new")

    def test_fallback_candidate_fetch_is_bounded(self):
        tool = _search_tool_module()
        fake_graph = FakeGraph()

        def query(cypher, params=None):
            fake_graph.queries.append((cypher, params or {}))
            return []
        fake_graph.query = query

        fake_embeddings = MagicMock()
        fake_embeddings.embed_query.return_value = [0.1]

        with patch.object(tool, "graph", fake_graph):
            tool._search_chunks(fake_embeddings, "tenant_1", "some search text", [], {})

        candidate_limit_params = [p.get("candidate_limit") for _, p in fake_graph.queries if "candidate_limit" in p]
        self.assertTrue(candidate_limit_params, "fallback fetch must be bounded, not unbounded")
        self.assertTrue(all(v == CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT for v in candidate_limit_params))


if __name__ == "__main__":
    unittest.main()
