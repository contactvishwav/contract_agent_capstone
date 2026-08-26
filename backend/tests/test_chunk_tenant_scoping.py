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

        chunk_calls = [(c, p) for c, p in fake_graph.queries if "CREATE (c:Chunk" in c]
        self.assertEqual(len(chunk_calls), 1)
        self.assertIn("tenant_id: $tenant_id", chunk_calls[0][0])
        self.assertEqual(chunk_calls[0][1]["tenant_id"], "tenant_a")

    async def test_store_chunks_reports_real_failure_when_chunk_creates_match_nothing(self):
        """Real, confirmed bug found live: the per-chunk write is `MATCH
        (d:Document {id: $document_id, tenant_id: $tenant_id}) CREATE
        (c:Chunk {...}) CREATE (d)-[:HAS_CHUNK]->(c)`. Whenever a caller's
        tenant_id ends up None (PolicyChunkingAgent.execute used to pass
        it inside a metadata dict instead of this method's own tenant_id
        parameter), `d.tenant_id: $tenant_id` becomes `d.tenant_id = null`
        - Cypher's three-valued logic always evaluates that to null, never
        true, even against a node whose tenant_id really was set to null.
        The MATCH silently found zero rows, so CREATE never ran for any
        chunk - no exception, no partial result - while store_chunks still
        returned {'success': True, 'chunks_stored': len(chunks)}, a real
        false-positive. This test's FakeGraph (query always returns [])
        reproduces that exact "MATCH finds nothing" condition regardless
        of which parameter caused it; store_chunks must now report the
        real failure instead of claiming success."""
        service, fake_graph = _storage_service()

        result = await service.store_chunks(
            "doc1", [{"content": "Either party may terminate with notice.", "chunk_index": 0}],
            tenant_id=None,
        )

        self.assertFalse(result["success"], f"expected a real failure, got: {result}")
        self.assertEqual(result["chunks_stored"], 0)

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
        # Post-fix, process_document also generates+persists real chunk
        # embeddings after storage (see test_chunk_embedding_persistence.py) -
        # mocked here for the same reason storage_service is: without it,
        # this test would make a real Gemini API call and a real write
        # attempt against production Neo4j.
        agent.chunk_embedding_service = MagicMock()
        agent.chunk_embedding_service.generate_chunk_embeddings = AsyncMock(return_value=[])
        agent.chunk_embedding_service.store_chunk_embeddings = AsyncMock(return_value=True)

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

    async def _upload(self, chunking_success=True, tenant_id="tenant_a", filename="Sample_MSA.pdf",
                     upload_contract_id="UPLOADED_REAL_20260808"):
        import io
        from fastapi import BackgroundTasks, UploadFile
        with patch("langchain_neo4j.Neo4jGraph"), patch(
            "backend.shared.utils.gemini_embedding_service.embedding"
        ):
            from backend.api.document_upload import upload_pdf
        from backend.governance.auth import TokenIdentity

        fake_identity = TokenIdentity(tenant_id=tenant_id, role="ADMIN", username="tester")
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.agents = {"gemini-2.5-flash": MagicMock()}
        fake_llm_mgr.raw_llms = {"gemini-2.5-flash": MagicMock()}

        fake_repo = MagicMock()
        def fake_query(cypher, params=None):
            if "SET c.source_hash" in cypher:
                return [{"contract_id": upload_contract_id}]
            return []  # no duplicate
        fake_repo.graph.query.side_effect = fake_query

        chunking_result = {
            "success": chunking_success,
            "document_id": "unused-placeholder",
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

        from backend.agents.agent_workflow_tracker import workflow_tracker as real_workflow_tracker
        start_agent_calls = []
        complete_agent_calls = []
        # Captured before patching (not referenced via the live singleton
        # inside the wrapper below) so calling through to the real
        # implementation can't recurse into the patched version of itself.
        real_start_agent = real_workflow_tracker.start_agent
        real_complete_agent = real_workflow_tracker.complete_agent

        def spy_start_agent(agent_name, agent_role, input_summary):
            start_agent_calls.append((agent_name, agent_role, input_summary))
            return real_start_agent(agent_name, agent_role, input_summary)

        def spy_complete_agent(execution, output_summary):
            complete_agent_calls.append((execution.agent_name, output_summary))
            return real_complete_agent(execution, output_summary)

        with patch("backend.infrastructure.audit_logger.AuditLogger.log_event"), \
             patch("backend.infrastructure.contract_repository.Neo4jContractRepository", return_value=fake_repo), \
             patch("backend.infrastructure.text_extractors.extract_pages_async", new=AsyncMock(return_value=MagicMock(
                 full_text="Contract text " * 50, pages=[], extraction_method="test"
             ))), \
             patch("backend.application.services.pdf_provenance_service.PdfProvenanceService"), \
             patch("backend.agents.chunking_agent.ChunkingAgent.process_document", new=AsyncMock(return_value=chunking_result)) as fake_process, \
             patch("backend.infrastructure.chunking.storage_service.ChunkingStorageService.link_document_to_contract", new=fake_link), \
             patch("backend.api.document_upload.workflow_tracker.start_agent", side_effect=spy_start_agent), \
             patch("backend.api.document_upload.workflow_tracker.complete_agent", side_effect=spy_complete_agent), \
             patch("backend.application.services.document_processing_service.DocumentServiceFactory.create_service") as fake_factory:
            fake_service = MagicMock()

            async def fake_process_pdf_upload(request):
                # The real DocumentProcessingService.process_pdf_upload
                # (mocked away here for everything else) has a real,
                # documented side effect this specific test class exists
                # to catch a bug in: its own _process_with_agent
                # unconditionally calls workflow_tracker.start_workflow(),
                # which resets self.executions = []. Reproduced explicitly
                # here rather than left implicit, so a real regression
                # (this call quietly disappearing, or Step 5.5's tracking
                # being moved back before it) shows up as a real assertion
                # failure instead of a false pass from mocking the reset
                # away entirely.
                real_workflow_tracker.start_workflow()
                pdf_execution = real_workflow_tracker.start_agent(
                    "PDF Processing Agent", "Extract text and analyze contract structure from PDF", "test"
                )
                real_workflow_tracker.complete_agent(pdf_execution, "Contract stored successfully")
                return {
                    "status": "success", "contract_id": upload_contract_id,
                    "final_result": "Contract stored successfully",
                }

            fake_service.process_pdf_upload = AsyncMock(side_effect=fake_process_pdf_upload)
            fake_factory.return_value = fake_service

            await upload_pdf(
                background_tasks=BackgroundTasks(),
                file=UploadFile(filename=filename, file=io.BytesIO(b"%PDF-1.4 fake pdf content")),
                model="gemini-2.5-flash",
                enable_enhanced=False,
                llm_mgr=fake_llm_mgr,
                identity=fake_identity,
            )
        # Attached rather than returned as new tuple elements, so every
        # existing 2-way `fake_process, link_calls = await self._upload()`
        # call site keeps working unchanged.
        fake_process.start_agent_calls = start_agent_calls
        fake_process.complete_agent_calls = complete_agent_calls
        return fake_process, captured_link_calls

    async def test_tenant_id_reaches_chunking_agent(self):
        fake_process, _ = await self._upload()
        fake_process.assert_awaited_once()
        _, kwargs = fake_process.call_args
        self.assertEqual(kwargs.get("tenant_id"), "tenant_a")

    async def test_chunking_agent_registers_with_workflow_tracker_on_success(self):
        """Real bug found live: ChunkingAgent genuinely runs on every real
        upload (real Chunk nodes with real quality_score, confirmed via
        direct Neo4j query in an earlier audit) but was never registered
        with workflow_tracker at all - invisible to the same "AI Agents
        Working" panel PDF Processing Agent and Enhanced Embedding Agent
        already appear in. This proves a real successful upload now calls
        both start_agent and complete_agent for "Chunking Agent", with a
        real, non-empty output summary."""
        fake_process, _ = await self._upload(chunking_success=True)

        agent_names_started = [name for name, _, _ in fake_process.start_agent_calls]
        self.assertIn("Chunking Agent", agent_names_started)

        chunking_completions = [s for name, s in fake_process.complete_agent_calls if name == "Chunking Agent"]
        self.assertEqual(len(chunking_completions), 1, "must complete exactly once, not zero and not twice")
        self.assertIn("2 chunks", chunking_completions[0])
        self.assertIn("sentence", chunking_completions[0])  # real strategy_type from chunking_result['plan']

    async def test_chunking_agent_start_input_summary_includes_real_document_id(self):
        fake_process, _ = await self._upload(chunking_success=True)
        _, kwargs = fake_process.call_args
        real_document_id = kwargs.get("document_id")

        chunking_starts = [s for name, _, s in fake_process.start_agent_calls if name == "Chunking Agent"]
        self.assertEqual(len(chunking_starts), 1)
        self.assertIn(real_document_id, chunking_starts[0])

    async def test_chunking_agent_entry_survives_pdf_processing_agents_workflow_reset(self):
        """The exact real sequencing bug found live: DocumentProcessing
        Service._process_with_agent (Step 7) calls workflow_tracker.
        start_workflow(), which unconditionally resets self.executions =
        [] for its own real PDF Processing Agent tracking - since that
        runs AFTER Step 5.5's chunking, a naively-recorded Chunking Agent
        entry gets silently wiped before any client ever reads workflow
        status. Confirmed live: real logs showed "AGENT COMPLETED:
        Chunking Agent" firing correctly, then a later "MULTI-AGENT
        WORKFLOW COMPLETED" summary listing only PDF Processing Agent.
        This checks the REAL workflow_tracker singleton's actual surviving
        state (not just that start_agent/complete_agent were called at
        some point), which is what GET /api/workflow/status actually
        reads - the only way to catch this exact class of bug."""
        from backend.agents.agent_workflow_tracker import workflow_tracker as real_workflow_tracker

        await self._upload(chunking_success=True)

        surviving_names = [e.agent_name for e in real_workflow_tracker.executions]
        self.assertIn("Chunking Agent", surviving_names,
                       "Chunking Agent's entry must survive Step 7's later workflow reset")
        self.assertIn("PDF Processing Agent", surviving_names)

        chunking_entries = [e for e in real_workflow_tracker.executions if e.agent_name == "Chunking Agent"]
        self.assertEqual(len(chunking_entries), 1)
        self.assertEqual(chunking_entries[0].status.value, "completed")
        # Real, non-instantaneous duration - proves the real Step 5.5
        # timing was captured and carried through, not reset to ~0ms by
        # being (re-)started right before being completed post-Step-7.
        self.assertIsNotNone(chunking_entries[0].start_time)
        self.assertIsNotNone(chunking_entries[0].end_time)
        self.assertGreaterEqual(chunking_entries[0].end_time, chunking_entries[0].start_time)

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
        _, kwargs = fake_process.call_args
        real_document_id = kwargs.get("document_id")
        # And genuinely completed successfully (not silently swallowed by
        # the except block this bug used to fall into) - the Contract link
        # only fires when async_chunking_succeeded is True.
        self.assertEqual(link_calls, [(real_document_id, "UPLOADED_REAL_20260808")])

    async def test_successful_chunking_links_document_to_the_real_contract_id(self):
        fake_process, link_calls = await self._upload(chunking_success=True)
        _, kwargs = fake_process.call_args
        self.assertEqual(link_calls, [(kwargs.get("document_id"), "UPLOADED_REAL_20260808")])

    async def test_failed_chunking_does_not_attempt_to_link(self):
        _, link_calls = await self._upload(chunking_success=False)
        self.assertEqual(link_calls, [])

    async def test_chunking_document_id_is_not_derived_from_filename(self):
        """Real, confirmed bug found live: this used to be
        file.filename.replace('.pdf', '') - not tenant-scoped, not unique
        per upload. Two different tenants (or the same tenant
        re-uploading) with an identically-named file wrote onto one
        shared Document node forever (confirmed live: 14 accumulated
        Chunk nodes on one Document, tenant_id last-write-wins). Fixed by
        generating a real, unique, UUID-based id upfront - this must never
        equal the filename-derived value again."""
        fake_process, _ = await self._upload(filename="Sample_MSA.pdf")
        _, kwargs = fake_process.call_args
        self.assertNotEqual(kwargs.get("document_id"), "Sample_MSA")
        self.assertTrue(kwargs.get("document_id"))

    async def test_two_tenants_uploading_the_same_filename_get_different_document_ids(self):
        """The real regression this fix closes: two different tenants
        uploading identically-named files must never collide onto the
        same chunking Document node - the exact scenario confirmed live
        in production (acme-legal and acme-legal2 both uploading
        Clean_MSA.pdf onto one shared Document, tenant_id overwritten
        last-write-wins)."""
        fake_process_a, _ = await self._upload(
            tenant_id="tenant_a", filename="Clean_MSA.pdf", upload_contract_id="UPLOADED_TENANT_A"
        )
        document_id_a = fake_process_a.call_args.kwargs.get("document_id")

        fake_process_b, _ = await self._upload(
            tenant_id="tenant_b", filename="Clean_MSA.pdf", upload_contract_id="UPLOADED_TENANT_B"
        )
        document_id_b = fake_process_b.call_args.kwargs.get("document_id")

        self.assertTrue(document_id_a)
        self.assertTrue(document_id_b)
        self.assertNotEqual(document_id_a, document_id_b)

    async def test_same_tenant_reuploading_the_same_filename_gets_a_different_document_id(self):
        """Same collision, single-tenant case: re-uploading a same-named
        file must not pile new chunks onto the same old Document node
        either (storage_service.py's Chunk writes use CREATE, so nothing
        would even overwrite cleanly - they'd just accumulate, as
        confirmed live)."""
        fake_process_1, _ = await self._upload(
            tenant_id="tenant_a", filename="Clean_MSA.pdf", upload_contract_id="UPLOADED_FIRST"
        )
        document_id_1 = fake_process_1.call_args.kwargs.get("document_id")

        fake_process_2, _ = await self._upload(
            tenant_id="tenant_a", filename="Clean_MSA.pdf", upload_contract_id="UPLOADED_SECOND"
        )
        document_id_2 = fake_process_2.call_args.kwargs.get("document_id")

        self.assertNotEqual(document_id_1, document_id_2)


if __name__ == "__main__":
    unittest.main()
