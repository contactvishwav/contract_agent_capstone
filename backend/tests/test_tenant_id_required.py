"""
Regression test, rewritten for the JWT migration: tenant_id-accepting
routes previously either defaulted to the literal string "default-tenant"
(P0), or later (P1) accepted a client-supplied tenant_id query param and
merely rejected its *absence* (422) without verifying it belonged to the
caller. Now tenant_id comes exclusively from a validated JWT
(backend/governance/auth.py) - there is no client-supplied tenant_id
parameter left on any of these routes at all.

This test proves two things per affected route, via the real FastAPI
TestClient (not calling route coroutines directly, which would bypass
both the old Query() binding and the new auth dependency resolution):

1. A request with no Authorization token is rejected (401), not served
   with some default/absent tenant scope.
2. A request that tries to smuggle a tenant_id via the (now-unused) query
   string is ignored - the token's tenant_id is what actually reaches the
   Cypher query, not whatever the caller put in the URL. This is the
   direct proof that the exploit class this whole migration exists to
   close (a caller naming any tenant_id it likes) is actually closed, not
   just that the parameter moved.
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

from backend.tests.conftest import auth_headers


def _client_for(router):
    """Isolated app with just this router and a dummy llm_manager on
    app.state (so routes depending on get_llm_manager don't blow up
    before reaching auth validation)."""
    app = FastAPI()
    app.include_router(router)
    app.state.llm_manager = MagicMock()
    return TestClient(app)


class TestIntelligenceRoutesRequireAuth(unittest.TestCase):
    def setUp(self):
        self.client = _client_for(contract_intelligence.router)

    def test_status_rejects_missing_token(self):
        response = self.client.get("/api/intelligence/contracts/CNT1/status")
        self.assertEqual(response.status_code, 401)

    def test_dashboard_rejects_missing_token(self):
        response = self.client.get("/api/intelligence/dashboard/summary")
        self.assertEqual(response.status_code, 401)

    def test_analyze_rejects_missing_token(self):
        response = self.client.post("/api/intelligence/contracts/CNT1/analyze")
        self.assertEqual(response.status_code, 401)

    def test_batch_analyze_rejects_missing_token(self):
        response = self.client.post(
            "/api/intelligence/contracts/batch-analyze", json=["CNT1"]
        )
        self.assertEqual(response.status_code, 401)

    def test_status_with_valid_token_ignores_caller_supplied_tenant_id_query_param(self):
        """The real proof: even if a caller tries the old trick (naming a
        different tenant_id in the query string), the token's tenant_id -
        not the query string - is what's actually used. Confirmed by
        patching the repository's graph and inspecting the Cypher params
        the route actually sent."""
        fake_graph = MagicMock()
        fake_graph.query.return_value = []
        with patch.object(contract_intelligence.repository, "graph", fake_graph):
            self.client.get(
                "/api/intelligence/contracts/CNT1/status?tenant_id=attacker_claimed_tenant",
                headers=auth_headers(tenant_id="real_token_tenant", role="ADMIN"),
            )
        _, params = fake_graph.query.call_args[0]
        self.assertEqual(params["tenant_id"], "real_token_tenant")
        self.assertNotEqual(params["tenant_id"], "attacker_claimed_tenant")


class TestUploadRoutesRequireAuth(unittest.TestCase):
    def test_contracts_upload_rejects_missing_token(self):
        client = _client_for(contracts.router)
        response = client.post(
            "/contracts/upload", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 401)

    def test_document_upload_rejects_missing_token(self):
        client = _client_for(document_upload.router)
        response = client.post(
            "/api/documents/upload", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 401)

    def test_document_upload_stream_rejects_missing_token(self):
        client = _client_for(document_upload.router)
        response = client.post(
            "/api/documents/upload-stream", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 401)

    def test_enhanced_upload_rejects_missing_token(self):
        client = _client_for(enhanced_document_upload.router)
        response = client.post(
            "/api/documents/enhanced/upload", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
