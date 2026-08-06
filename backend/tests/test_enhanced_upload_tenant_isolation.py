"""
Regression test for a real tenant-isolation bug found live during GCP
deployment verification: POST /api/documents/enhanced/upload silently
stored every contract under tenant_id="default-tenant" regardless of the
uploader's real tenant, because EnhancedDocumentProcessingService.
_process_with_enhanced_embeddings's `initial_state` never included
`tenant_id` at all - so pdf_processing_agent.py's store_contract_node
(state.get("tenant_id", "default-tenant")) always fell back to the
default. A subsequent tenant-scoped read (e.g. analyze_contract_by_id,
which matches Contract {file_id, tenant_id} exactly) for the real
uploader's tenant then found nothing, since the contract was actually
filed under a different tenant_id than the one it was uploaded under.

This is the exact same class of bug commit 7b7ac9a fixed for the regular
upload path (document_processing_service.py's initial_state already sets
"tenant_id": request.tenant_id or "default-tenant") - that fix was never
mirrored into the enhanced upload path. This test proves the mirror fix:
the tenant_id actually reaching the PDF processing agent's initial state
must match the request's real tenant_id, not silently collapse to the
default for every tenant.

Follows the EnhancedDocumentProcessingService mocking pattern established
in test_neo4j_connection_reuse.py (EmbeddingOrchestrator/EmbeddingValidator/
PDFAgentFactory mocked out; only the constructor and the state threading
under test are real).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.application.services.enhanced_document_processing_service import (
        EnhancedDocumentProcessingService,
    )
    from backend.domain.entities import DocumentProcessingRequest


def _make_service_with_captured_agent():
    """Builds a real EnhancedDocumentProcessingService with its heavy
    dependencies mocked out, and a fake PDF agent whose ainvoke captures
    whatever initial_state it was actually called with."""
    agent_manager = MagicMock()

    with patch("backend.application.services.enhanced_document_processing_service.EmbeddingOrchestrator"), \
         patch("backend.application.services.enhanced_document_processing_service.EmbeddingValidator"):
        service = EnhancedDocumentProcessingService(agent_manager)

    captured = {}

    async def fake_ainvoke(initial_state):
        captured["initial_state"] = initial_state
        return {"processing_result": None, "extracted_text": ""}

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(side_effect=fake_ainvoke)
    service.pdf_agent_factory.create_agent = MagicMock(return_value=fake_agent)

    return service, captured


class EnhancedUploadTenantIdThreadingTests(unittest.TestCase):
    """os.path.exists is mocked True throughout: process_pdf_with_embeddings's
    only use of the real filesystem is its up-front existence check, which
    is irrelevant to the tenant_id threading behavior under test here."""

    def test_real_tenant_id_reaches_the_pdf_agent_initial_state(self):
        service, captured = _make_service_with_captured_agent()
        request = DocumentProcessingRequest(
            file_path="/fake/upload/contract.pdf",
            filename="contract.pdf",
            tenant_id="tenant_acme_corp",
            processing_options={"model": "gemini-2.5-flash"},
        )

        with patch("backend.application.services.enhanced_document_processing_service.os.path.exists", return_value=True):
            asyncio.run(service.process_pdf_with_embeddings(request))

        self.assertEqual(
            captured["initial_state"].get("tenant_id"), "tenant_acme_corp",
            "The uploader's real tenant_id must reach the PDF agent's initial state, "
            "not silently fall back to default-tenant",
        )

    def test_different_requests_get_their_own_distinct_tenant_id(self):
        service, captured = _make_service_with_captured_agent()
        request = DocumentProcessingRequest(
            file_path="/fake/upload/contract.pdf",
            filename="contract.pdf",
            tenant_id="tenant_globex_inc",
            processing_options={"model": "gemini-2.5-flash"},
        )

        with patch("backend.application.services.enhanced_document_processing_service.os.path.exists", return_value=True):
            asyncio.run(service.process_pdf_with_embeddings(request))

        self.assertEqual(captured["initial_state"].get("tenant_id"), "tenant_globex_inc")

    def test_missing_tenant_id_falls_back_to_default_tenant(self):
        service, captured = _make_service_with_captured_agent()
        request = DocumentProcessingRequest(
            file_path="/fake/upload/contract.pdf",
            filename="contract.pdf",
            tenant_id="",
            processing_options={"model": "gemini-2.5-flash"},
        )

        with patch("backend.application.services.enhanced_document_processing_service.os.path.exists", return_value=True):
            asyncio.run(service.process_pdf_with_embeddings(request))

        self.assertEqual(captured["initial_state"].get("tenant_id"), "default-tenant")


if __name__ == "__main__":
    unittest.main()
