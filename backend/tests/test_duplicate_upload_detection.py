"""
Regression tests for a real, confirmed bug found live while verifying the
duplicate-upload response-shape fix (test_duplicate_upload_response_shape.py):
uploading the exact same PDF twice never actually got flagged as a
duplicate at all - two unrelated Contract nodes were silently created
every time.

Root cause: document_upload.py's duplicate check matched
`WHERE c.file_id CONTAINS $filename`, but Contract.file_id is a random
UUID-based id (contract_repository.py's
f"UPLOADED_{uuid4().hex[:8]}_{date}") that never contains the original
filename - so the match could never succeed against any real Contract
node. There was also no tenant_id in the old query at all, which would
have been a cross-tenant duplicate-detection leak once the filename match
was fixed on its own.

The filename still survives for display, while duplicate identity is now the
uploaded byte-content SHA-256 scoped by tenant and active lifecycle. This lets
a corrected document reuse a filename and lets an archived document be
uploaded again.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.contract_repository import Neo4jContractRepository
    from backend.domain.value_objects import ContractData


class ContractRepositoryStoresFilenameTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_contract_writes_filename_property(self):
        repo = Neo4jContractRepository()
        repo.graph = MagicMock()
        repo.graph.query.return_value = [{"contract_id": "UPLOADED_abc123_20260807"}]
        repo.embedding_service = MagicMock()
        repo.embedding_service.embed_query.return_value = []

        await repo.store_contract(
            {
                "is_contract": True,
                "confidence_score": 0.95,
                "contract_type": "MSA",
                "summary": "A summary",
                "parties": [],
                "full_text": "some contract text",
                "filename": "Salesforce_MSA.pdf",
            },
            tenant_id="tenant_a",
        )

        # First call is the CREATE (c:Contract ...) query - the one this
        # bug lives in. Later calls (party/governing-law relationships)
        # aren't relevant here.
        create_call = repo.graph.query.call_args_list[0]
        cypher, params = create_call.args[0], create_call.args[1]
        self.assertIn("filename", cypher)
        self.assertEqual(params["filename"], "Salesforce_MSA.pdf")
        self.assertEqual(params["tenant_id"], "tenant_a")


class PdfProcessingAgentThreadsFilenameTests(unittest.IsolatedAsyncioTestCase):
    """Confirms filename survives PDFProcessingState -> ContractData ->
    the dict handed to the repository, not just that the repository
    itself would store it if given one."""

    async def test_store_contract_node_passes_filename_through(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.pdf_processing_agent import get_pdf_processing_agent

        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(content='''{
            "is_contract": true,
            "confidence_score": 0.95,
            "contract_type": "MSA",
            "summary": "A real contract summary over twenty characters",
            "parties": [],
            "key_terms": []
        }''')

        captured = {}

        async def fake_store_contract(data_dict, tenant_id, contract_id=None):
            captured["data_dict"] = data_dict
            return contract_id or "UPLOADED_captured_20260807"

        # pdf_processing_agent.py does `from ...contract_repository import
        # Neo4jContractRepository`, binding its own local reference - must
        # patch the name in THIS module's namespace, not the origin module.
        with patch("backend.agents.pdf_processing_agent.Neo4jContractRepository") as MockRepo:
            MockRepo.return_value.store_contract = AsyncMock(side_effect=fake_store_contract)
            agent = get_pdf_processing_agent(fake_llm)

            initial_state = {
                "file_path": "/fake/path.pdf",
                "tenant_id": "tenant_a",
                "extracted_text": "already extracted text " * 10,
                "contract_data": None,
                "processing_result": None,
                "filename": "Salesforce_MSA.pdf",
            }
            await agent.ainvoke(initial_state)

        self.assertEqual(captured.get("data_dict", {}).get("filename"), "Salesforce_MSA.pdf")


class DocumentUploadDuplicateQueryTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the real upload_pdf route function to confirm the actual
    Cypher query/params used for duplicate detection - not just that the
    response is shaped correctly once a duplicate happens to be found."""

    async def _upload(self, existing_matches, tenant_id="tenant_a", filename="Salesforce_MSA.pdf"):
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
        captured_calls = []

        def fake_query(cypher, params):
            captured_calls.append((cypher, params))
            return existing_matches

        fake_repo.graph.query.side_effect = fake_query

        provenance = MagicMock()
        provenance.source_record.return_value = {"storage_key": "already-retained"}
        with patch("backend.infrastructure.audit_logger.AuditLogger.log_event"), \
             patch("backend.infrastructure.contract_repository.Neo4jContractRepository", return_value=fake_repo), \
             patch(
                 "backend.application.services.pdf_provenance_service.PdfProvenanceService",
                 return_value=provenance,
             ):
            try:
                result = await upload_pdf(
                    background_tasks=BackgroundTasks(),
                    file=UploadFile(filename=filename, file=io.BytesIO(b"%PDF-1.4 fake pdf content")),
                    model="gemini-2.5-flash",
                    enable_enhanced=False,
                    llm_mgr=fake_llm_mgr,
                    identity=fake_identity,
                )
            except Exception:
                # A non-duplicate upload proceeds past the duplicate check
                # into real text extraction, which fails on this fake,
                # unparseable PDF content - irrelevant here, since these
                # tests only care about what the duplicate check itself
                # queried before that point.
                result = None
        return result, captured_calls

    async def test_duplicate_check_queries_by_content_hash_tenant_and_active_lifecycle(self):
        # existing_matches=[] means the route proceeds past the duplicate
        # check into a real (failing, on this fake PDF) extraction step,
        # whose own error handling makes further, unrelated graph.query
        # calls via ErrorTracker - irrelevant here, so this only asserts
        # on the FIRST call, which is always the duplicate check itself.
        _, calls = await self._upload(existing_matches=[])
        self.assertGreaterEqual(len(calls), 1)
        cypher, params = calls[0]
        self.assertNotIn("CONTAINS", cypher)
        self.assertIn("source_hash", cypher)
        self.assertIn("lifecycle_status", cypher)
        self.assertEqual(len(params["source_hash"]), 64)
        self.assertEqual(params["tenant_id"], "tenant_a")

    async def test_a_real_match_is_reported_as_duplicate_with_the_existing_contract_id(self):
        result, _ = await self._upload(existing_matches=[{"file_id": "UPLOADED_existing_20260807"}])
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["contract_id"], "UPLOADED_existing_20260807")


if __name__ == "__main__":
    unittest.main()
