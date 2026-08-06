"""
HTTP-level tests for the org-invite endpoints (credential-provisioning
engagement, docs design report): POST /api/auth/invites (admin-only, own-
tenant-only), GET /api/auth/invites/{token} (public preview), POST
/api/auth/invites/{token}/accept (public, consumes the invite).

The invite-repository layer's own single-use/expiry guarantees are
covered directly in test_invite_repository.py; this file proves the
route-level authorization (who can create an invite, and for which
tenant) and the actual vulnerability fix: tenant_id/role on the created
account come from the server-side invite record, never from the client's
accept-request body.
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.api import auth_api

from backend.tests.conftest import auth_headers
from backend.tests.test_invite_repository import FakeInviteGraph
from backend.tests.test_user_repository import FakeUserGraph


class OrgInviteApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._user_patcher = patch.object(auth_api._user_repository, "graph", FakeUserGraph())
        self._user_patcher.start()
        self.addCleanup(self._user_patcher.stop)
        self._invite_patcher = patch.object(auth_api._invite_repository, "graph", FakeInviteGraph())
        self._invite_patcher.start()
        self.addCleanup(self._invite_patcher.stop)
        # Real email sends aren't the point of these tests - swap in a
        # fake that never touches the network, same as any other
        # external-call boundary in this test suite.
        self._email_patcher = patch.object(
            auth_api, "_email_service",
            MagicMock(send_invite_email=MagicMock(return_value=MagicMock(sent=True, reason=""))),
        )
        self._email_patcher.start()
        self.addCleanup(self._email_patcher.stop)

    def _create_invite(self, tenant_id="tenant_a", role="VIEWER", email="bob@example.com"):
        return self.client.post(
            "/api/auth/invites", json={"email": email, "role": role},
            headers=auth_headers(tenant_id=tenant_id, role="ADMIN"),
        )

    # -- creation / RBAC --------------------------------------------------

    def test_admin_can_create_an_invite_for_their_own_tenant(self):
        response = self._create_invite(tenant_id="tenant_a")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["tenant_id"], "tenant_a")

    def test_non_admin_cannot_create_an_invite(self):
        response = self.client.post(
            "/api/auth/invites", json={"email": "bob@example.com", "role": "VIEWER"},
            headers=auth_headers(tenant_id="tenant_a", role="VIEWER"),
        )
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_caller_cannot_create_an_invite(self):
        response = self.client.post("/api/auth/invites", json={"email": "bob@example.com", "role": "VIEWER"})
        self.assertEqual(response.status_code, 401)

    def test_invite_tenant_id_comes_from_the_caller_token_not_the_request_body(self):
        """InviteCreateRequest has no tenant_id field at all - an admin
        cannot request an invite into a tenant they don't belong to, since
        there's nowhere in the request to even try. Confirmed here by
        checking the created invite's tenant_id matches the ADMIN token's
        tenant, not anything the payload could have smuggled in."""
        response = self._create_invite(tenant_id="admins_own_tenant")
        self.assertEqual(response.json()["tenant_id"], "admins_own_tenant")

    def test_invalid_role_is_rejected(self):
        response = self.client.post(
            "/api/auth/invites", json={"email": "bob@example.com", "role": "NOT_A_REAL_ROLE"},
            headers=auth_headers(tenant_id="tenant_a", role="ADMIN"),
        )
        self.assertEqual(response.status_code, 400)

    # -- preview ------------------------------------------------------

    def test_preview_shows_tenant_and_role_without_consuming(self):
        # The HTTP create response deliberately never echoes the raw token
        # back (it only ever goes out via email) - go through the
        # repository directly so this test has the raw token to preview.
        raw_token = auth_api._invite_repository.create_invite(
            email="carol@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )

        preview = self.client.get(f"/api/auth/invites/{raw_token}")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["tenant_id"], "tenant_a")
        self.assertEqual(preview.json()["role"], "VIEWER")

        # Still previewable a second time - preview must not consume it.
        preview_again = self.client.get(f"/api/auth/invites/{raw_token}")
        self.assertEqual(preview_again.status_code, 200)

    def test_preview_of_unknown_token_is_404(self):
        response = self.client.get("/api/auth/invites/totally-made-up-token")
        self.assertEqual(response.status_code, 404)

    # -- accept: the actual vulnerability fix --------------------------

    def test_accept_creates_account_with_invites_tenant_and_role_not_client_supplied(self):
        raw_token = auth_api._invite_repository.create_invite(
            email="carol@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )

        response = self.client.post(
            f"/api/auth/invites/{raw_token}/accept", json={"username": "carol", "password": "carols-password-9!"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["tenant_id"], "tenant_a")
        self.assertEqual(response.json()["role"], "VIEWER")

    def test_invite_for_tenant_a_cannot_be_used_to_gain_a_role_in_tenant_b(self):
        """The core tenant-isolation property: an invite is intrinsically
        bound to one tenant at creation - there is no accept-time
        parameter that could redirect it elsewhere."""
        raw_token = auth_api._invite_repository.create_invite(
            email="carol@example.com", tenant_id="tenant_a", role="ADMIN", invited_by="admin_x",
        )

        response = self.client.post(
            f"/api/auth/invites/{raw_token}/accept", json={"username": "carol", "password": "carols-password-9!"},
        )
        # No tenant_id field exists on InviteAcceptRequest for a caller to
        # even attempt smuggling a different tenant through - the created
        # account can only ever land in the invite's own tenant_id.
        self.assertEqual(response.json()["tenant_id"], "tenant_a")
        self.assertNotEqual(response.json()["tenant_id"], "tenant_b")

    def test_accepting_an_already_used_invite_is_rejected(self):
        raw_token = auth_api._invite_repository.create_invite(
            email="carol@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )
        first = self.client.post(
            f"/api/auth/invites/{raw_token}/accept", json={"username": "carol", "password": "carols-password-9!"},
        )
        self.assertEqual(first.status_code, 201)

        second = self.client.post(
            f"/api/auth/invites/{raw_token}/accept", json={"username": "someone_else", "password": "another-password-9!"},
        )
        self.assertEqual(second.status_code, 404)

    def test_accepting_an_expired_invite_is_rejected(self):
        raw_token = auth_api._invite_repository.create_invite(
            email="carol@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x", ttl_days=-1,
        )

        response = self.client.post(
            f"/api/auth/invites/{raw_token}/accept", json={"username": "carol", "password": "carols-password-9!"},
        )
        self.assertEqual(response.status_code, 404)

    def test_accepting_with_a_tampered_token_is_rejected(self):
        raw_token = auth_api._invite_repository.create_invite(
            email="carol@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )
        tampered = raw_token[:-1] + ("A" if raw_token[-1] != "A" else "B")

        response = self.client.post(
            f"/api/auth/invites/{tampered}/accept", json={"username": "carol", "password": "carols-password-9!"},
        )
        self.assertEqual(response.status_code, 404)

    def test_accept_rejects_duplicate_username(self):
        auth_api._user_repository.create_user(username="carol", password="existing-password-9!", tenant_id="tenant_x", role="VIEWER")
        raw_token = auth_api._invite_repository.create_invite(
            email="carol@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )

        response = self.client.post(
            f"/api/auth/invites/{raw_token}/accept", json={"username": "carol", "password": "carols-password-9!"},
        )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
