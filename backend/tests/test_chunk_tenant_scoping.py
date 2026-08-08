"""
Regression tests for a real, confirmed bug: the primary/async chunking
pipeline (backend/agents/chunking_agent.py's ChunkingAgent.process_document,
run on every real PDF upload via backend/api/document_upload.py's Step 5.5)
wrote Document/Chunk nodes with no tenant_id property at all, and never
linked to the real Contract node - so every real, tenant-scoped chunk
search (chunk_embedding_service.py's search_similar_chunks, and the
actually-reachable one Contract Chat uses, enhanced_contract_search_tool.py's
_search_chunks, both filtering on `d.tenant_id = $tenant_id`) silently
found nothing, for every tenant, always.

A second, independent, real bug was found live while fixing this: Step 5.5's
own success-log line accessed `chunking_result['plan'].strategy_type` -
attribute access on a value that's actually a plain dict on this code path
(ChunkingAgent.process_document's orchestrator-success branch returns
`'plan': {'strategy_type': ..., ...}`), raising AttributeError on every
single successful async chunking result. That exception was caught by
Step 5.5's own except block and misreported as "Async chunking failed",
discarding the already-correctly-stored chunks and triggering a wasted,
duplicate sync-fallback write under the same document_id every time - so
even fixing tenant_id alone would have stayed invisible without this fix
too, since the async path never actually got credit for succeeding.
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


def _storage_service():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        from backend.infrastructure.chunking.storage_service import ChunkingStorageService
    service = ChunkingStorageService()
    fake_graph = FakeGraph()
    service.graph = fake_graph
    service.chunk_embedding_service = MagicMock()
    return service, fake_graph


class ChunkingStorageServiceTenantScopingTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_chunks_sets_tenant_id_on_document_node(self):
        service, fake_graph = _storage_service()

        await service.store_chunks(
            "doc1", [{"content": "Either party may terminate with notice.", "chunk_index": 0}],
            tenant_id="tenant_a",
        )

        merge_calls = [(c, p) for c, p in fake_graph.queries if "MERGE (d:Document" in c]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("tenant_id", merge_calls[0][0], "the Cypher must actually SET d.tenant_id")
        self.assertEqual(merge_calls[0][1]["tenant_id"], "tenant_a")

    async def test_link_document_to_contract_writes_the_real_contract_id(self):
        service, fake_graph = _storage_service()

        await service.link_document_to_contract("Salesforce_MSA", "UPLOADED_ABC123_20260808")

        self.assertEqual(len(fake_graph.queries), 1)
        cypher, params = fake_graph.queries[0]
        self.assertIn("d.contract_id", cypher)
        self.assertEqual(params["document_id"], "Salesforce_MSA")
        self.assertEqual(params["contract_id"], "UPLOADED_ABC123_20260808")

    async def test_link_document_to_contract_failure_does_not_raise(self):
        """Best-effort, matching document_upload.py's own try/except around
        this call - a linking failure must never break the real upload."""
        service, _ = _storage_service()
        service.graph = MagicMock()
        service.graph.query.side_effect = RuntimeError("boom")

        await service.link_document_to_contract("doc1", "UPLOADED_ABC123")  # must not raise


class ChunkingAgentTenantThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_document_threads_tenant_id_to_storage_service(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.chunking_agent import ChunkingAgent

        agent = ChunkingAgent(embedding_service=MagicMock())
        agent.storage_service = MagicMock()
        agent.storage_service.store_chunks = AsyncMock(return_value={"success": True})

        fake_result = MagicMock()
        fake_result.success = True
        fake_result.chunks = [{"content": "text", "chunk_index": 0}]
        fake_result.strategy_used = "sentence"
        fake_result.fallback_chain = ["sentence"]
        fake_result.quality_metrics = {"overall_quality": 0.9}
        fake_result.embedding_results = None
        fake_result.performance_metrics = {}

        with patch("backend.infrastructure.chunking.chunking_orchestrator.ChunkingOrchestrator") as MockOrch, \
             patch("backend.infrastructure.chunking.chunking_orchestrator.ChunkingCommandFactory") as MockFactory:
            MockOrch.return_value.execute_chunking = AsyncMock(return_value=fake_result)
            MockFactory.create_document_upload_command.return_value = MagicMock()

            await agent.process_document("doc1", "contract text", metadata={"filename": "x.pdf"}, tenant_id="tenant_a")

        agent.storage_service.store_chunks.assert_awaited_once()
        _, kwargs = agent.storage_service.store_chunks.call_args
        self.assertEqual(kwargs.get("tenant_id"), "tenant_a")


class DocumentUploadChunkingWiringTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the real upload_pdf route function to prove Step 5.5
    genuinely threads tenant_id through, the strategy_type dict-access
    bug is fixed (no false "async failed" fallback on a real success),
    and the post-Step-7 Contract link actually fires with the real ids."""

    async def _upload(self, chunking_success=True):
        import io
        from fastapi import BackgroundTasks, UploadFile
        from backend.api.document_upload import upload_pdf
        from backend.governance.auth import TokenIdentity

        fake_identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN", username="tester")
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.agents = {"gemini-2.5-flash": MagicMock()}

        fake_repo = MagicMock()
        fake_repo.graph.query.return_value = []  # no duplicate

        chunking_result = {
            "success": chunking_success,
            "document_id": "Sample_MSA",
            "chunk_count": 2,
            "plan": {"strategy_type": "sentence", "fallback_chain": ["sentence"], "reasoning": "x"},
            # Real shape from quality_validator.py's QualityValidator.
            # validate_chunks - no top-level 'overall_quality' key exists;
            # per-chunk scores nest under 'chunk_scores'. A second real,
            # confirmed bug (found live, after fixing the first
            # 'plan'-shape one) assumed 'overall_quality' existed here and
            # crashed with KeyError on every real successful async
            # chunking result.
            "quality_assessment": {
                "total_chunks": 2, "passed": 2, "failed": 0,
                "chunk_scores": [
                    {"chunk_id": "c0", "scores": {"overall": 0.9}},
                    {"chunk_id": "c1", "scores": {"overall": 0.8}},
                ],
            },
            "document_analysis": {},
        }

        captured_link_calls = []

        async def fake_link(self, document_id, contract_id):
            captured_link_calls.append((document_id, contract_id))

        with patch("backend.infrastructure.audit_logger.AuditLogger.log_event"), \
             patch("backend.infrastructure.contract_repository.Neo4jContractRepository", return_value=fake_repo), \
             patch("backend.infrastructure.text_extractors.extract_text_async", new=AsyncMock(return_value="Contract text " * 50)), \
             patch("backend.agents.chunking_agent.ChunkingAgent.process_document", new=AsyncMock(return_value=chunking_result)) as fake_process, \
             patch("backend.infrastructure.chunking.storage_service.ChunkingStorageService.link_document_to_contract", new=fake_link), \
             patch("backend.application.services.document_processing_service.DocumentServiceFactory.create_service") as fake_factory:
            fake_service = MagicMock()
            fake_service.process_pdf_upload = AsyncMock(return_value={
                "status": "success", "contract_id": "UPLOADED_REAL_20260808",
                "final_result": "Contract stored successfully",
            })
            fake_factory.return_value = fake_service

            await upload_pdf(
                background_tasks=BackgroundTasks(),
                file=UploadFile(filename="Sample_MSA.pdf", file=io.BytesIO(b"%PDF-1.4 fake pdf content")),
                model="gemini-2.5-flash",
                enable_enhanced=False,
                llm_mgr=fake_llm_mgr,
                identity=fake_identity,
            )
        return fake_process, captured_link_calls

    async def test_tenant_id_reaches_chunking_agent(self):
        fake_process, _ = await self._upload()
        fake_process.assert_awaited_once()
        _, kwargs = fake_process.call_args
        self.assertEqual(kwargs.get("tenant_id"), "tenant_a")

    async def test_successful_async_chunking_does_not_crash_on_real_result_shape(self):
        """Regression for both real bugs found live in Step 5.5's success-
        logging block: chunking_result['plan'].strategy_type (attribute
        access on what's actually a dict) and chunking_result[
        'quality_assessment']['overall_quality'] (a key that never
        actually exists in the real shape - see chunking_result's
        'chunk_scores' comment above). If _upload's real route code
        regressed to either shape assumption, this call would raise and
        this test would fail here, before even reaching the assertion."""
        fake_process, link_calls = await self._upload(chunking_success=True)
        fake_process.assert_awaited_once()  # got this far without an unhandled exception
        # And genuinely completed successfully (not silently swallowed by
        # the except block this bug used to fall into) - the Contract link
        # only fires when async_chunking_succeeded is True.
        self.assertEqual(link_calls, [("Sample_MSA", "UPLOADED_REAL_20260808")])

    async def test_successful_chunking_links_document_to_the_real_contract_id(self):
        _, link_calls = await self._upload(chunking_success=True)
        self.assertEqual(link_calls, [("Sample_MSA", "UPLOADED_REAL_20260808")])

    async def test_failed_chunking_does_not_attempt_to_link(self):
        _, link_calls = await self._upload(chunking_success=False)
        self.assertEqual(link_calls, [])


if __name__ == "__main__":
    unittest.main()
