"""
Regression test for a real production incident: uploading a PDF with the
frontend's "Multi-Level Embeddings" checkbox unchecked (the plain
/api/documents/upload path, DocumentProcessingService) always hit a real
AttributeError - 'DocumentProcessingService' object has no attribute
'_process_enhanced_embeddings' - confirmed live via a timestamped
production log line. The call was leftover copy-paste from
EnhancedDocumentProcessingService (which does define that method); this
class was never meant to attempt multi-level embedding generation at all -
that's the entire point of the checkbox being unchecked. The exception was
silently swallowed (caught, logged as a WARNING, upload still reported
"success"), so every plain-path upload looked fine on the surface while
quietly failing this one step every single time.

Fixed by removing the call (and the now-unused embedding_orchestrator/
embedding_validator instantiation in __init__) entirely, rather than
implementing the missing method - the plain path should genuinely skip
multi-level embeddings, not attempt and fail at them. This test asserts
the plain path completes with a real, honest success and never logs the
old "Enhanced embedding processing failed" warning.
"""
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.application.services.document_processing_service import (
    DocumentProcessingService,
)
from backend.domain.entities import DocumentProcessingRequest
from backend.domain.value_objects import ProcessingResult, ProcessingStatus


class DocumentProcessingServicePlainPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent_manager = MagicMock()
        self.agent_manager.get_raw_model_by_name.return_value = MagicMock()
        self.service = DocumentProcessingService(self.agent_manager)

    async def test_plain_upload_completes_successfully_with_no_attribute_error(self):
        """The exact real path: a successful extraction/storage result,
        with real extracted_text present (the condition that used to
        trigger the broken _process_enhanced_embeddings call). Simply
        completing without raising AttributeError is itself the assertion -
        pre-fix, this call would raise inside _process_with_agent's own
        try/except and get reported as a WARNING, not propagate as a test
        failure, so the real regression check is in the next test (no
        warning logged) combined with this one (a real, complete result)."""
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(return_value={
            "processing_result": ProcessingResult(
                status=ProcessingStatus.SUCCESS,
                contract_id="UPLOADED_TESTCONTRACT",
                message="Contract stored successfully",
            ),
            "extracted_text": "This is a real contract body with actual text content.",
            "contract_data": None,
        })
        self.service.pdf_agent_factory.create_agent = MagicMock(return_value=fake_agent)

        request = DocumentProcessingRequest(
            file_path="/tmp/does-not-need-to-exist-for-this-mock.pdf",
            filename="plain_path_test.pdf",
            tenant_id="test_tenant",
            processing_options={"model": "gemini-2.5-flash"},
            contract_id="UPLOADED_TESTCONTRACT",
        )

        result = await self.service._process_with_agent(fake_agent, request)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["contract_id"], "UPLOADED_TESTCONTRACT")
        self.assertIsNone(result["error"])

    async def test_plain_upload_never_logs_the_old_enhanced_embedding_warning(self):
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(return_value={
            "processing_result": ProcessingResult(
                status=ProcessingStatus.SUCCESS,
                contract_id="UPLOADED_TESTCONTRACT",
                message="Contract stored successfully",
            ),
            "extracted_text": "This is a real contract body with actual text content.",
            "contract_data": None,
        })
        self.service.pdf_agent_factory.create_agent = MagicMock(return_value=fake_agent)

        request = DocumentProcessingRequest(
            file_path="/tmp/does-not-need-to-exist-for-this-mock.pdf",
            filename="plain_path_test.pdf",
            tenant_id="test_tenant",
            processing_options={"model": "gemini-2.5-flash"},
            contract_id="UPLOADED_TESTCONTRACT",
        )

        logger = logging.getLogger("backend.application.services.document_processing_service")
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        logger.addHandler(handler)
        try:
            result = await self.service._process_with_agent(fake_agent, request)
        finally:
            logger.removeHandler(handler)

        # The real bug: a genuine AttributeError, caught and reported only
        # as this exact warning message. Its absence, combined with a real
        # success result below, is the honest signal the fix worked -
        # not a silently-swallowed failure wearing a success status.
        warning_messages = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        self.assertFalse(
            any("Enhanced embedding processing failed" in m for m in warning_messages),
            f"plain upload path must never attempt (or fail at) enhanced embeddings; saw: {warning_messages}",
        )
        self.assertFalse(
            any("_process_enhanced_embeddings" in m for m in warning_messages),
            f"the removed method must never be referenced in a live warning; saw: {warning_messages}",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["contract_id"], "UPLOADED_TESTCONTRACT")
        self.assertIsNone(result["error"])

    async def test_plain_upload_has_no_embedding_orchestrator_or_validator(self):
        """The now-unused EmbeddingOrchestrator/EmbeddingValidator instances
        must actually be gone, not just unreferenced - confirms this is a
        real removal, not a partially-applied one."""
        self.assertFalse(hasattr(self.service, "embedding_orchestrator"))
        self.assertFalse(hasattr(self.service, "embedding_validator"))
        self.assertFalse(hasattr(self.service, "_process_enhanced_embeddings"))

    async def test_plain_upload_reports_honest_error_when_storage_genuinely_fails(self):
        """Not touched by this fix, but must still hold: a real storage
        failure (no processing_result) is reported as a real error, never
        silently upgraded to success."""
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(return_value={
            "processing_result": None,
            "contract_data": None,
        })
        self.service.pdf_agent_factory.create_agent = MagicMock(return_value=fake_agent)

        request = DocumentProcessingRequest(
            file_path="/tmp/does-not-need-to-exist-for-this-mock.pdf",
            filename="plain_path_fail_test.pdf",
            tenant_id="test_tenant",
            processing_options={"model": "gemini-2.5-flash"},
            contract_id="UPLOADED_TESTCONTRACT_FAIL",
        )

        result = await self.service._process_with_agent(fake_agent, request)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["contract_id"])


if __name__ == "__main__":
    unittest.main()
