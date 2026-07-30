"""
Regression fix (live-infrastructure audit): this file previously did no
mocking at all - `from backend.main import app` transitively constructs a
real Neo4jGraph() (backend/shared/utils/contract_search_tool.py:54) and a
real Redis connection attempt (backend/shared/cache/redis_cache.py) with
zero patching. It only worked because pytest's alphabetical collection
order left an earlier test file's Neo4jGraph mock cached in sys.modules by
the time this file's tests ran - run it alone and it would attempt a real
Neo4j connection.
"""

import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph") as _MockNeo4jGraph, \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    _MockNeo4jGraph.return_value.query.return_value = []
    from backend.main import app
    from backend.governance.rbac import UserRole, Permission

class TestRBACIntegrated(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_admin_access_all(self):
        """ADMIN should have access to everything"""
        # Test Search
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers={"X-User-Role": UserRole.ADMIN},
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        
        # Test Audit
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": UserRole.ADMIN}
        )
        self.assertNotEqual(response.status_code, 403)

    def test_viewer_restricted_access(self):
        """VIEWER should be restricted from sensitive actions"""
        # Test Search (ALLOWED)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers={"X-User-Role": UserRole.VIEWER},
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        
        # Test Audit Trail (DENIED)
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": UserRole.VIEWER}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("VIEW_AUDIT", response.json()["detail"])

    def test_legal_reviewer_access(self):
        """LEGAL_REVIEWER should have analysis and upload permissions"""
        # Test Search (ALLOWED)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers={"X-User-Role": UserRole.LEGAL_REVIEWER},
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        
        # Test Audit Trail (DENIED)
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": UserRole.LEGAL_REVIEWER}
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_role_blocked(self):
        """Request with invalid role should be blocked (401)"""
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": "HACKER"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid user role", response.json()["detail"])

    def test_missing_role_defaults_to_viewer(self):
        """Request without header should default to VIEWER (if configured) or block"""
        # In our implementation it defaults to VIEWER in get_current_user_role
        # Let's verify a VIEWER action (search) and an ADMIN action (audit)
        
        # Search (ALLOWED for VIEWER)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        
        # Audit (DENIED for VIEWER)
        response = self.client.get("/api/audit/trail/test-resource")
        self.assertEqual(response.status_code, 403)

if __name__ == "__main__":
    unittest.main()
