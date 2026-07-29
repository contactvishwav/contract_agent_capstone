"""
Regression test: tenant_id-accepting routes previously defaulted to the
literal string "default-tenant" whenever a caller omitted it (Query(default=
"default-tenant", ...)). Since there is no authentication layer establishing
a verified caller identity yet (see docs/ENTERPRISE_READINESS.md P0/P1 notes),
a request with the tenant_id parameter simply left off would silently read
or write another tenant's data bucket instead of failing loudly.

This test proves that omitting tenant_id is now rejected (422) rather than
silently substituted, for every affected route. It exercises the real
FastAPI query-parameter validation layer via TestClient - calling the route
coroutines directly (as done elsewhere in this suite) would bypass Query()
binding entirely and could not detect this class of bug.
"""

import unittest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api import contract_intelligence
    from backend.api import contracts
    from backend.api import document_upload
    from backend.api import enhanced_document_upload

ADMIN = {"X-User-Role": "ADMIN"}


def _client_for(router):
    """Isolated app with just this router, an ADMIN role (so RBAC never
    intervenes) and a dummy llm_manager on app.state (so routes depending on
    get_llm_manager don't blow up before reaching tenant_id validation)."""
    app = FastAPI()
    app.include_router(router)
    app.state.llm_manager = MagicMock()
    client = TestClient(app)
    client.headers.update(ADMIN)
    return client


class TestIntelligenceRoutesRequireTenantId(unittest.TestCase):
    def setUp(self):
        self.client = _client_for(contract_intelligence.router)

    def test_status_rejects_missing_tenant_id(self):
        response = self.client.get("/api/intelligence/contracts/CNT1/status")
        self.assertEqual(response.status_code, 422)
        self.assertNotEqual(response.status_code, 200)

    def test_dashboard_rejects_missing_tenant_id(self):
        response = self.client.get("/api/intelligence/dashboard/summary")
        self.assertEqual(response.status_code, 422)

    def test_analyze_rejects_missing_tenant_id(self):
        response = self.client.post("/api/intelligence/contracts/CNT1/analyze")
        self.assertEqual(response.status_code, 422)

    def test_batch_analyze_rejects_missing_tenant_id(self):
        response = self.client.post(
            "/api/intelligence/contracts/batch-analyze", json=["CNT1"]
        )
        self.assertEqual(response.status_code, 422)


class TestUploadRoutesRequireTenantId(unittest.TestCase):
    def test_contracts_upload_rejects_missing_tenant_id(self):
        client = _client_for(contracts.router)
        response = client.post(
            "/contracts/upload", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 422)

    def test_document_upload_rejects_missing_tenant_id(self):
        client = _client_for(document_upload.router)
        response = client.post(
            "/api/documents/upload", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 422)

    def test_document_upload_stream_rejects_missing_tenant_id(self):
        client = _client_for(document_upload.router)
        response = client.post(
            "/api/documents/upload-stream", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 422)

    def test_enhanced_upload_rejects_missing_tenant_id(self):
        client = _client_for(enhanced_document_upload.router)
        response = client.post(
            "/api/documents/enhanced/upload", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
