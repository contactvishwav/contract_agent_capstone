"""
Unit tests for Enhanced Search MINOR phase items 8 & 9:
Item 8: Clean empty state in Enhanced Search UI without debug string.
Item 9: Enhanced-uploaded contracts retain real filename in Neo4j and document list.
"""

import unittest
from unittest.mock import MagicMock, patch
from backend.domain.entities import DocumentProcessingRequest


class EnhancedSearchMinorFixesTests(unittest.TestCase):

    def test_item_9_initial_state_includes_filename(self):
        """Item 9: initial_state in process_pdf_with_embeddings must include request.filename"""
        from backend.application.services.enhanced_document_processing_service import EnhancedDocumentProcessingService

        mock_agent_manager = MagicMock()
        service = EnhancedDocumentProcessingService(mock_agent_manager)

        # Mock PDF agent
        mock_pdf_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.contract_id = "doc_test_filename_123"
        mock_result.status.value = "success"
        mock_pdf_agent.ainvoke = MagicMock()

        async def mock_ainvoke(state):
            self.assertEqual(state.get("filename"), "Master_Services_Agreement_2025.pdf")
            self.assertEqual(state.get("tenant_id"), "tenant-minor-9")
            return {
                "processing_result": mock_result,
                "extracted_text": "Sample text",
                "filename": state.get("filename")
            }

        mock_pdf_agent.ainvoke.side_effect = mock_ainvoke
        service.pdf_agent_factory.create_agent = MagicMock(return_value=mock_pdf_agent)
        service._process_enhanced_embeddings = MagicMock(return_value=True)
        service._cleanup_file = MagicMock()

        request = DocumentProcessingRequest(
            file_path="/tmp/fake.pdf",
            filename="Master_Services_Agreement_2025.pdf",
            tenant_id="tenant-minor-9",
            processing_options={"model": "gemini-2.5-flash", "enable_embeddings": True}
        )

        with patch("os.path.exists", return_value=True):
            import asyncio
            res = asyncio.run(service.process_pdf_with_embeddings(request))

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["filename"], "Master_Services_Agreement_2025.pdf")
        self.assertEqual(res["contract_id"], "doc_test_filename_123")

    def test_item_9_enhanced_upload_finalization_sets_filename(self):
        """Item 9: upload_pdf_enhanced finalization query updates c.filename"""
        from backend.api.enhanced_document_upload import upload_pdf_enhanced
        from backend.governance.auth import TokenIdentity

        mock_file = MagicMock()
        mock_file.filename = "Vendor_SOW_Final.pdf"

        async def mock_read():
            return b"%PDF-1.4 sample pdf content for minor test"

        mock_file.read = mock_read

        mock_llm_mgr = MagicMock()
        mock_llm_mgr.raw_llms = {"gemini-2.5-flash": MagicMock()}

        identity = TokenIdentity(tenant_id="tenant-minor-final", username="admin", role="ADMIN")

        mock_repo = MagicMock()
        mock_repo.graph.query.side_effect = [
            [],  # duplicate check -> no existing
            [{"contract_id": "doc_sow_999"}]  # finalization query -> success
        ]

        mock_service = MagicMock()
        async def mock_process(req):
            return {
                "status": "success",
                "contract_id": "doc_sow_999",
                "final_result": "SUCCESS: Contract stored with ID: doc_sow_999",
                "filename": req.filename
            }
        mock_service.process_pdf_with_embeddings = mock_process

        with patch("backend.model_registry.validate_model"), \
             patch("backend.infrastructure.contract_repository.Neo4jContractRepository", return_value=mock_repo), \
             patch("backend.infrastructure.text_extractors.extract_pages_async", return_value=MagicMock(full_text="Sample text")), \
             patch("backend.application.services.enhanced_document_processing_service.EnhancedDocumentServiceFactory.create_service", return_value=mock_service), \
             patch("backend.application.services.pdf_provenance_service.PdfProvenanceService"):
            import asyncio
            result = asyncio.run(upload_pdf_enhanced(
                file=mock_file,
                model="gemini-2.5-flash",
                enable_embeddings=True,
                llm_mgr=mock_llm_mgr,
                identity=identity
            ))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["filename"], "Vendor_SOW_Final.pdf")
        self.assertEqual(result["contract_id"], "doc_sow_999")

        # Verify finalization query contains c.filename update
        final_call = mock_repo.graph.query.call_args_list[1]
        cypher_query, params = final_call.args
        self.assertIn("c.filename = coalesce(c.filename, $filename)", cypher_query)
        self.assertEqual(params["filename"], "Vendor_SOW_Final.pdf")


if __name__ == "__main__":
    unittest.main()
