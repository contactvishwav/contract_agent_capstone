"""
Regression test for a real, confirmed bug found live: POST
/api/documents/upload's duplicate-filename branch (document_upload.py)
used to return only {message, filename, status, existing_contract_id,
action} - omitting contract_id, details, and model_used.

frontend/src/pages/IntelligencePage.tsx only renders the analysis panel
when uploadResult.contract_id is truthy, and DocumentUpload.tsx's status
message fell through to its generic default ("Processing completed:
${result.details}") with details undefined - so re-uploading a
same-named file (a very real thing to happen mid-demo) dead-ended on the
placeholder "Upload a contract to begin analysis" panel with a garbled
"Processing completed: undefined" message, instead of showing the
contract that was already uploaded.

Fix: the duplicate branch now returns contract_id (set to the existing
contract's id, so the UI can proceed straight to it), details, and
model_used, alongside the original existing_contract_id/action fields
kept for compatibility.
"""
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import BackgroundTasks, UploadFile

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api.document_upload import upload_pdf
    from backend.governance.auth import TokenIdentity


def _fake_upload_file(filename: str, content: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content))


class DuplicateUploadResponseShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_response_includes_everything_the_frontend_needs(self):
        fake_identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN", username="tester")
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.agents = {"gemini-2.5-flash": MagicMock()}
        fake_llm_mgr.raw_llms = {"gemini-2.5-flash": MagicMock()}

        fake_repo = MagicMock()
        fake_repo.graph.query.return_value = [{"file_id": "existing_contract_123"}]

        provenance = MagicMock()
        provenance.source_record.return_value = {"storage_key": "already-retained"}
        with patch("backend.infrastructure.audit_logger.AuditLogger.log_event"), \
             patch("backend.infrastructure.contract_repository.Neo4jContractRepository", return_value=fake_repo), \
             patch("backend.application.services.pdf_provenance_service.PdfProvenanceService", return_value=provenance):
            result = await upload_pdf(
                background_tasks=BackgroundTasks(),
                file=_fake_upload_file("Sample_MSA.pdf", b"%PDF-1.4 fake pdf content for duplicate test"),
                model="gemini-2.5-flash",
                enable_enhanced=False,
                llm_mgr=fake_llm_mgr,
                identity=fake_identity,
            )

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["contract_id"], "existing_contract_123")
        self.assertEqual(result["existing_contract_id"], "existing_contract_123")
        self.assertTrue(result["details"])
        self.assertEqual(result["model_used"], "gemini-2.5-flash")
        self.assertEqual(result["filename"], "Sample_MSA.pdf")


if __name__ == "__main__":
    unittest.main()
