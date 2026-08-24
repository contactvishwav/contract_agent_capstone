"""
Regression fix (live-infrastructure audit): this file previously did no
mocking at all - `from backend.main import app` transitively constructs a
real Neo4jGraph() (backend/shared/utils/contract_search_tool.py:54) and a
real Redis connection attempt (backend/shared/cache/redis_cache.py) with
zero patching. It only worked because pytest's alphabetical collection
order left an earlier test file's Neo4jGraph mock cached in sys.modules by
the time this file's tests ran - run it alone and it would attempt a real
Neo4j connection.

JWT migration: the old X-User-Role header mechanism is gone entirely -
every request now needs a real, signed Authorization: Bearer token
(backend/governance/auth.py). Uses the shared auth_headers helper
(backend/tests/conftest.py) rather than hand-building tokens per test.
"""

import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph") as _MockNeo4jGraph, \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    _MockNeo4jGraph.return_value.query.return_value = []
    from backend.main import app
    from backend.governance.rbac import UserRole, Permission

from backend.tests.conftest import auth_headers


class TestRBACIntegrated(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_admin_access_all(self):
        """ADMIN should have access to everything"""
        # Test Search
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers=auth_headers(role=UserRole.ADMIN.value),
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)

        # Test Audit
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers=auth_headers(role=UserRole.ADMIN.value)
        )
        self.assertNotEqual(response.status_code, 403)

    def test_viewer_restricted_access(self):
        """VIEWER should be restricted from sensitive actions"""
        # Test Search (ALLOWED)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers=auth_headers(role=UserRole.VIEWER.value),
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)

        # Test Audit Trail (DENIED)
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers=auth_headers(role=UserRole.VIEWER.value)
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("VIEW_AUDIT", response.json()["detail"])

    def test_legal_reviewer_access(self):
        """LEGAL_REVIEWER should have analysis and upload permissions"""
        # Test Search (ALLOWED)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers=auth_headers(role=UserRole.LEGAL_REVIEWER.value),
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)

        # Test Audit Trail (DENIED)
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers=auth_headers(role=UserRole.LEGAL_REVIEWER.value)
        )
        self.assertEqual(response.status_code, 403)

    def test_analyst_access(self):
        """Real bug found live: ANALYST (the auto-seeded/SSO-provisioned
        "demo" account's real role) previously had no UserRole member at
        all, so every one of these calls 401'd with "Invalid role claim in
        token" instead of a normal permission check - most concretely on
        GET /api/chat/sessions, confirmed live via a real production-like
        local run (a real login, followed by an automatic logout the
        instant the chat UI's session list fetch hit this same 401)."""
        # Test Search (ALLOWED - ANALYZE)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers=auth_headers(role=UserRole.ANALYST.value),
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        self.assertNotEqual(response.status_code, 401)

        # Test the exact real route that was found broken live
        response = self.client.get(
            "/api/chat/sessions",
            headers=auth_headers(role=UserRole.ANALYST.value)
        )
        self.assertNotEqual(response.status_code, 401)
        self.assertNotEqual(response.status_code, 403)

        # Test Audit Trail (DENIED - ANALYST never had VIEW_AUDIT, this
        # fix only adds the working set LEGAL_REVIEWER already has)
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers=auth_headers(role=UserRole.ANALYST.value)
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_role_claim_blocked(self):
        """A validly-signed token whose role claim isn't a real UserRole
        should be blocked (401) - was "invalid X-User-Role header" before
        the JWT migration; same outcome, different mechanism."""
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers=auth_headers(role="HACKER")
        )
        self.assertEqual(response.status_code, 401)

    def test_missing_token_is_401_not_a_default_role(self):
        """Was "request without header defaults to VIEWER" - the JWT
        migration deliberately removes that default entirely: no token
        means no access at all, not a silent minimal-permission fallback."""
        response = self.client.post(
            "/api/contracts/search/enhanced",
            json={"search_level": "document", "query": "test"}
        )
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/api/audit/trail/test-resource")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
