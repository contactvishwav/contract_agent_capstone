"""
HTTP-level tests for POST /api/auth/register's narrowed scope
(credential-provisioning engagement, docs design report, decision (a)):
self-service registration is bootstrap-only - it may create the first
user of a brand-new tenant_id, but a second registration attempt against
an already-provisioned tenant must be rejected (403), even with a
different, available username. This is the actual fix for the
vulnerability the fully-open version of this endpoint had: any caller
could previously self-register into ANY existing tenant_id with ADMIN.

infrastructure/user_repository.py's TenantBootstrapScopeTests already
covers the repository layer directly; this file proves the same behavior
through the real HTTP route (status codes, response bodies).
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.api import auth_api

from backend.tests.test_user_repository import FakeUserGraph


class RegisterBootstrapScopeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._patcher = patch.object(auth_api._user_repository, "graph", FakeUserGraph())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _register(self, username, tenant_id, role="ADMIN", password="correct-horse-battery-9!"):
        return self.client.post(
            "/api/auth/register", json={"username": username, "password": password, "tenant_id": tenant_id, "role": role},
        )

    def test_first_user_of_a_new_tenant_can_self_register(self):
        response = self._register("founder", "brand_new_tenant")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["tenant_id"], "brand_new_tenant")

    def test_second_registration_into_an_already_provisioned_tenant_is_rejected(self):
        first = self._register("founder", "existing_tenant")
        self.assertEqual(first.status_code, 201)

        second = self._register("someone_else_entirely", "existing_tenant", role="ADMIN")
        self.assertEqual(second.status_code, 403)
        self.assertIn("already has members", second.json()["detail"])

    def test_rejection_applies_regardless_of_requested_role(self):
        """Not just a re-registration-as-ADMIN check - even a caller
        requesting a low-privilege role can't self-register into an
        already-provisioned tenant; ALL new members from this point on
        must come through a real invite."""
        self._register("founder", "existing_tenant", role="ADMIN")

        response = self._register("low_priv_attempt", "existing_tenant", role="VIEWER")
        self.assertEqual(response.status_code, 403)

    def test_different_tenants_can_each_bootstrap_independently(self):
        first = self._register("founder_a", "tenant_a")
        second = self._register("founder_b", "tenant_b")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

    def test_duplicate_username_still_reports_409_not_403(self):
        """The more specific, actionable error takes priority - see
        UserRepository.create_user's docstring for the ordering rationale."""
        self._register("founder", "tenant_a")

        response = self._register("founder", "tenant_a")
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
