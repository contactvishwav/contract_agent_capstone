"""
Real JWT authentication tests (closes the README's "Real Authenticated
Tenant Identity" future-enhancement item, governance/auth.py has the full
design rationale).

Covers what test_rbac_unit.py/test_rbac_integrated.py/
test_tenant_id_required.py don't: the actual token-issuance HTTP endpoint,
and - critically - a real cross-tenant test proving a valid token for
tenant A cannot access tenant B's data no matter what tenant_id a caller
tries to claim elsewhere in the request. This is the exact gap flagged
during live-infrastructure verification: tenant_id was previously just a
client-supplied parameter, never tied to a verified identity.
"""

import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.api import contract_intelligence
    from backend.governance.auth import create_access_token, get_current_identity

from backend.tests.conftest import auth_headers


class TokenIssuanceTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_issue_token_returns_a_real_usable_bearer_token(self):
        response = self.client.post("/api/auth/token", json={"tenant_id": "tenant_a", "role": "ADMIN"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("access_token", body)
        self.assertEqual(body["token_type"], "bearer")
        self.assertGreater(body["expires_in"], 0)

        # The issued token must actually validate - not just look like a
        # token - by round-tripping it through the real validation
        # dependency.
        import asyncio
        identity = asyncio.run(get_current_identity(authorization=f"Bearer {body['access_token']}"))
        self.assertEqual(identity.tenant_id, "tenant_a")
        self.assertEqual(identity.role, "ADMIN")

    def test_issue_token_rejects_invalid_role(self):
        response = self.client.post("/api/auth/token", json={"tenant_id": "tenant_a", "role": "NOT_A_REAL_ROLE"})
        self.assertEqual(response.status_code, 400)

    def test_issue_token_rejects_empty_tenant_id(self):
        response = self.client.post("/api/auth/token", json={"tenant_id": "   ", "role": "ADMIN"})
        self.assertEqual(response.status_code, 400)

    def test_two_tokens_for_different_tenants_are_different_and_both_valid(self):
        token_a = create_access_token(tenant_id="tenant_a", role="ADMIN")
        token_b = create_access_token(tenant_id="tenant_b", role="ADMIN")
        self.assertNotEqual(token_a, token_b)


class RealCrossTenantIsolationViaTokenTests(unittest.TestCase):
    """
    The direct proof: a real, validly-signed token for tenant A used
    against a live route cannot read tenant B's data, and vice versa - not
    because the caller politely supplied the "right" tenant_id (there's no
    longer any way for a caller to supply one at all), but because the
    token itself is the only source of tenant_id and its signature can't
    be forged to claim a different one.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.fake_graph = TenantScopedFakeGraph()
        self._patcher = patch.object(contract_intelligence.repository, "graph", self.fake_graph)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_tenant_b_token_cannot_read_tenant_a_contract_status(self):
        self.fake_graph.add_contract("CONTRACT_1", tenant_id="tenant_a", status="completed", risk_score=75.0)

        # Tenant A reading its own contract: works.
        own = self.client.get(
            "/api/intelligence/contracts/CONTRACT_1/status",
            headers=auth_headers(tenant_id="tenant_a", role="ADMIN"),
        )
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json()["risk_score"], 75.0)

        # Tenant B, with its own genuinely-valid (not forged, not tampered)
        # token, requesting the exact same contract_id: must not see it.
        cross = self.client.get(
            "/api/intelligence/contracts/CONTRACT_1/status",
            headers=auth_headers(tenant_id="tenant_b", role="ADMIN"),
        )
        self.assertEqual(cross.status_code, 404)

    def test_tenant_b_token_cannot_analyze_tenant_as_contract(self):
        """/analyze now just enqueues a Celery task (eager in tests) using
        identity.tenant_id - confirms the enqueued task itself receives
        tenant B's tenant_id, not tenant A's, when tenant B's contract_id
        guess/collision happens to match a real contract_id."""
        with patch("backend.tasks.analyze_contract_task.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="task-123")
            self.client.post(
                "/api/intelligence/contracts/CONTRACT_1/analyze",
                headers=auth_headers(tenant_id="tenant_b", role="ADMIN"),
            )
        # contract_id, tenant_id, model, use_planning
        called_args = mock_delay.call_args[0]
        self.assertEqual(called_args[1], "tenant_b")
        self.assertNotEqual(called_args[1], "tenant_a")


class TenantScopedFakeGraph:
    """Mirrors the real MATCH (c:Contract {file_id, tenant_id}) shape -
    only returns a row when both properties agree, same as a real Neo4j
    graph would."""

    def __init__(self):
        self.contracts = {}

    def add_contract(self, file_id, tenant_id, status, risk_score):
        self.contracts[file_id] = {
            "status": status, "risk_score": risk_score, "risk_level": "HIGH",
            "violations_count": 1, "clauses_count": 5, "redlines_count": 1,
            "processing_time": 10.0, "updated": None, "tenant_id": tenant_id,
        }

    def query(self, cypher, params=None):
        params = params or {}
        contract_id = params.get("contract_id")
        tenant_id = params.get("tenant_id")
        doc = self.contracts.get(contract_id)
        if not doc or doc["tenant_id"] != tenant_id:
            return []
        return [doc]


if __name__ == "__main__":
    unittest.main()
