"""
Two real, confirmed bugs found live in the primary/async chunking
pipeline (ChunkingAgent.process_document -> ChunkingOrchestrator.
execute_chunking -> ChunkingStorageService.store_chunks), on top of
item 3's earlier tenant_id/linking fix (test_chunk_tenant_scoping.py).
Both were found while investigating why Contract Chat's chunk-level
search returned total_count: 0 for a freshly uploaded, correctly
tenant-scoped, correctly linked contract.

Bug 1 - embeddings never persisted: ChunkEmbeddingService.
store_chunk_embeddings() used to MATCH (d:Document {id: $document_id})
and CREATE a brand-new Chunk node with the embedding attached - but this
ran *inside* ChunkingOrchestrator.execute_chunking()'s old Step 9,
before ChunkingStorageService.store_chunks() (which actually MERGEs the
Document and CREATEs the real Chunk nodes) had ever run - store_chunks
only runs afterward, back in ChunkingAgent.process_document, once
execute_chunking() already returned. On a document being chunked for
the first time (the normal case), that MATCH silently found 0 rows, so
the embedding CREATE inside it never fired - no exception, nothing
logged, nothing to catch. Confirmed live in production Neo4j: every one
of 14 real Chunk nodes under one Document had embedding_ready: true but
embedding: null - invisible to chunk_embedding_vector_index (confirmed
ONLINE, indexing Chunk.embedding), so chunk-level semantic search could
never return a result for any tenant, regardless of item 3's tenant
fix.

Fixed by inverting the dependency: ChunkingAgent.process_document now
generates and persists embeddings strictly *after*
ChunkingStorageService.store_chunks() has created the real Chunk nodes,
and ChunkEmbeddingService.store_chunk_embeddings() now MATCHes the
already-existing Chunk by id and SETs its embedding, rather than
creating a second, duplicate, minimally-populated Chunk node that
depended on Document existing first.

Bug 2 - chunk_index always 0: the real orchestrator path
(ChunkingOrchestrator._execute_chunking_with_fallback) never set
chunk_index on chunk dicts at all - only a separate, legacy
ChunkingAgent._execute_chunking method does (used by the rarely-hit
_fallback_to_original_processing path, not this one). storage_service.
py's chunk.get('chunk_index', 0) therefore always defaulted to 0 for
every chunk of every document, colliding every chunk_id onto
"{document_id}_chunk_0". Confirmed live: 14 accumulated Chunk nodes on
one Document, every single one chunk_index: 0. Fixed by assigning
chunk_index inside ChunkingOrchestrator.execute_chunking itself, once
chunk ordering is final (after overlap analysis and performance
optimization, immediately before quality validation) - this is also
what makes bug 1's fix reliable: ChunkingStorageService.store_chunks and
ChunkEmbeddingService.generate_chunk_embeddings both derive chunk_id
from chunk_index using the identical f"{document_id}_chunk_{index}"
formula, so a correct, unique index is required for the two writes to
land on the same Chunk node at all.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeGraph:
    """Records every issued (cypher, params); returns [] by default."""

    def __init__(self):
        self.queries = []

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        return []


# ---------------------------------------------------------------------------
# Bug 1a: store_chunk_embeddings no longer depends on a Document node
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


class StoreChunkEmbeddingsMatchesExistingChunkTests(unittest.IsolatedAsyncioTestCase):
    """store_chunk_embeddings must attach to an *existing* Chunk node by
    id, never require or create a Document - the real ordering bug was
    exactly a MATCH (d:Document...) that ran before the Document
    existed, so the CREATE inside it silently never fired."""

    async def test_issues_a_match_on_chunk_id_not_a_create_via_document(self):
        service, fake_graph, ChunkEmbedding = _chunk_embedding_service()
        chunk_embedding = ChunkEmbedding(
            chunk_id="doc1_chunk_0", document_id="doc1", embedding=[0.1, 0.2, 0.3],
            chunk_content="content is owned by storage_service now, not this write", chunk_metadata={},
        )

        result = await service.store_chunk_embeddings([chunk_embedding])

        self.assertTrue(result)
        self.assertEqual(len(fake_graph.queries), 1)
        cypher, params = fake_graph.queries[0]
        self.assertIn("MATCH (c:Chunk", cypher)
        self.assertNotIn("CREATE (c:Chunk", cypher, "must not create a second, duplicate Chunk node")
        self.assertNotIn("Document", cypher, "must not depend on the Document node existing at all")
        self.assertEqual(params["chunk_id"], "doc1_chunk_0")
        self.assertEqual(params["embedding"], [0.1, 0.2, 0.3])

    async def test_succeeds_even_when_no_document_node_exists_in_the_graph(self):
        """Regression for the exact bug: previously, success secretly
        depended on a Document node existing first. A FakeGraph here
        never returns a Document (it returns [] to everything) - the new
        query has no Document dependency at all, so this must still
        succeed and issue the real write."""
        service, fake_graph, ChunkEmbedding = _chunk_embedding_service()
        chunk_embedding = ChunkEmbedding(
            chunk_id="doc_no_document_node_chunk_0", document_id="doc_no_document_node",
            embedding=[0.4, 0.5], chunk_content="x", chunk_metadata={},
        )

        result = await service.store_chunk_embeddings([chunk_embedding])

        self.assertTrue(result)
        self.assertEqual(fake_graph.queries[0][1]["embedding"], [0.4, 0.5])


# ---------------------------------------------------------------------------
# Bug 1b: ChunkingAgent.process_document persists embeddings AFTER storage
# ---------------------------------------------------------------------------

class ProcessDocumentEmbeddingOrderingTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the real ChunkingAgent.process_document (the actual
    Step 5.5 entry point used on every real PDF upload), proving it now
    generates and persists embeddings strictly after storage_service.
    store_chunks has created the real Chunk nodes - the reverse of the
    order this pipeline used before the fix."""

    async def test_embeddings_are_generated_and_stored_after_chunks_are_stored(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.chunking_agent import ChunkingAgent

        agent = ChunkingAgent(embedding_service=MagicMock())

        call_order = []

        async def fake_store_chunks(*args, **kwargs):
            call_order.append("store_chunks")
            return {"success": True}
        agent.storage_service.store_chunks = fake_store_chunks

        async def fake_generate_chunk_embeddings(chunks, document_id):
            call_order.append("generate_chunk_embeddings")
            return [MagicMock(chunk_id=f"{document_id}_chunk_{c['chunk_index']}") for c in chunks]
        agent.chunk_embedding_service.generate_chunk_embeddings = fake_generate_chunk_embeddings

        async def fake_store_chunk_embeddings(chunk_embeddings):
            call_order.append("store_chunk_embeddings")
            return True
        agent.chunk_embedding_service.store_chunk_embeddings = fake_store_chunk_embeddings

        fake_result = MagicMock()
        fake_result.success = True
        fake_result.chunks = [
            {"content": "chunk a", "chunk_index": 0},
            {"content": "chunk b", "chunk_index": 1},
        ]
        fake_result.strategy_used = "sentence"
        fake_result.fallback_chain = ["sentence"]
        fake_result.quality_metrics = {}
        fake_result.performance_metrics = {}

        with patch("backend.infrastructure.chunking.chunking_orchestrator.ChunkingOrchestrator") as MockOrch, \
             patch("backend.infrastructure.chunking.chunking_orchestrator.ChunkingCommandFactory") as MockFactory:
            MockOrch.return_value.execute_chunking = AsyncMock(return_value=fake_result)
            MockFactory.create_document_upload_command.return_value = MagicMock()

            result = await agent.process_document(
                "doc1", "contract text", metadata={"filename": "x.pdf"}, tenant_id="tenant_a"
            )

        # The real regression: pre-fix, this pipeline never called
        # generate/store_chunk_embeddings at all after storage - call_order
        # would only ever contain "store_chunks".
        self.assertEqual(call_order, ["store_chunks", "generate_chunk_embeddings", "store_chunk_embeddings"])
        self.assertTrue(result["embedding_storage_result"])
        self.assertEqual(result["embedding_metrics"], 2)


# ---------------------------------------------------------------------------
# Bug 2: chunk_index is set uniquely by the real orchestrator path
# ---------------------------------------------------------------------------

class OrchestratorChunkIndexAssignmentTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the real ChunkingOrchestrator.execute_chunking end to
    end (real strategy selection, real chunking, no mocks needed - this
    step never touches Neo4j or an LLM anymore after bug 1's fix), on
    content long enough to force multiple chunks."""

    MULTI_SECTION_CONTRACT = "\n\n".join([
        "1. TERM. This Agreement shall commence on the Effective Date and continue "
        "for a period of thirty-six (36) months unless earlier terminated in "
        "accordance with the provisions set forth herein.",
        "2. PAYMENT TERMS. Client shall pay Provider a monthly fee of $10,000, due "
        "net thirty (30) days from the date of invoice. Late payments shall accrue "
        "interest at a rate of 1.5% per month.",
        "3. CONFIDENTIALITY. Each party agrees to hold in confidence all "
        "proprietary information disclosed by the other party and shall not "
        "disclose such information to any third party without prior written consent.",
        "4. INDEMNIFICATION. Each party shall indemnify, defend, and hold harmless "
        "the other party from and against any claims, damages, or liabilities "
        "arising out of a breach of this Agreement.",
        "5. TERMINATION. Either party may terminate this Agreement for convenience "
        "upon sixty (60) days written notice to the other party.",
    ] * 3)  # repeated to comfortably clear any minimum chunk-count/size thresholds

    async def test_real_orchestrator_assigns_unique_sequential_chunk_index(self):
        from backend.infrastructure.chunking.chunking_orchestrator import (
            ChunkingOrchestrator, ChunkingCommandFactory,
        )

        orchestrator = ChunkingOrchestrator()
        command = ChunkingCommandFactory.create_document_upload_command(
            "doc1", self.MULTI_SECTION_CONTRACT, "multi_section.pdf"
        )

        result = await orchestrator.execute_chunking(command)

        self.assertTrue(result.success)
        self.assertGreaterEqual(len(result.chunks), 2, "test content must actually produce multiple chunks")

        indices = [chunk.get("chunk_index") for chunk in result.chunks]
        self.assertNotIn(None, indices, "every chunk must have chunk_index set")
        self.assertEqual(len(indices), len(set(indices)), "chunk_index must be unique per chunk - regression for the always-0 bug")
        self.assertEqual(indices, list(range(len(result.chunks))), "chunk_index must be sequential, matching real storage order")

    async def test_chunk_ids_derived_from_index_are_therefore_unique(self):
        """The real, user-visible consequence of bug 2: storage_service.py
        and chunk_embedding_service.py both build
        chunk_id = f"{document_id}_chunk_{chunk_index}" - with the old
        always-0 bug, every chunk of every document collided onto the
        same id."""
        from backend.infrastructure.chunking.chunking_orchestrator import (
            ChunkingOrchestrator, ChunkingCommandFactory,
        )

        orchestrator = ChunkingOrchestrator()
        command = ChunkingCommandFactory.create_document_upload_command(
            "doc1", self.MULTI_SECTION_CONTRACT, "multi_section.pdf"
        )

        result = await orchestrator.execute_chunking(command)

        chunk_ids = [f"doc1_chunk_{chunk.get('chunk_index', 0)}" for chunk in result.chunks]
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)), "chunk ids must not collide")


if __name__ == "__main__":
    unittest.main()
