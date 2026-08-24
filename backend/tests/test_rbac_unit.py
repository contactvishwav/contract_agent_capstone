import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from fastapi import HTTPException

# Add current directory to path
sys.path.insert(0, os.getcwd())

from backend.governance.rbac import RBACManager, UserRole, Permission, requires_permission
from backend.governance.auth import get_current_identity, create_access_token, TokenIdentity


class TestJWTIdentity(unittest.IsolatedAsyncioTestCase):
    """
    Regression coverage for the JWT migration: the old get_current_user_role
    (X-User-Role header, defaulted to VIEWER on a missing header despite its
    own comment claiming "for safety") is gone entirely - replaced by
    get_current_identity, which validates a signed token and has no default
    at all. A missing/invalid/expired/tampered token is always a 401, never
    a silent fallback to any role.
    """

    async def test_missing_authorization_header_is_401_not_a_default_role(self):
        with self.assertRaises(HTTPException) as cm:
            await get_current_identity(authorization=None)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_malformed_authorization_header_is_401(self):
        with self.assertRaises(HTTPException) as cm:
            await get_current_identity(authorization="not-a-bearer-token")
        self.assertEqual(cm.exception.status_code, 401)

    async def test_valid_token_resolves_real_tenant_id_and_role(self):
        token = create_access_token(tenant_id="tenant_a", role="ADMIN")
        identity = await get_current_identity(authorization=f"Bearer {token}")
        self.assertEqual(identity.tenant_id, "tenant_a")
        self.assertEqual(identity.role, "ADMIN")

    async def test_tampered_token_is_rejected(self):
        token = create_access_token(tenant_id="tenant_a", role="ADMIN")
        tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]
        with self.assertRaises(HTTPException) as cm:
            await get_current_identity(authorization=f"Bearer {tampered}")
        self.assertEqual(cm.exception.status_code, 401)

    async def test_expired_token_is_rejected(self):
        from datetime import timedelta
        token = create_access_token(tenant_id="tenant_a", role="ADMIN", expires_delta=timedelta(seconds=-1))
        with self.assertRaises(HTTPException) as cm:
            await get_current_identity(authorization=f"Bearer {token}")
        self.assertEqual(cm.exception.status_code, 401)
        self.assertIn("expired", cm.exception.detail.lower())

    async def test_token_signed_with_a_different_secret_is_rejected(self):
        """Proves signature verification is real, not just structural JSON
        decoding - a token this service never signed must be rejected even
        if it's otherwise well-formed."""
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta

        forged = pyjwt.encode(
            {"tenant_id": "tenant_a", "role": "ADMIN",
             "iat": datetime.now(timezone.utc), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "a-completely-different-secret-the-service-never-used",
            algorithm="HS256",
        )
        with self.assertRaises(HTTPException) as cm:
            await get_current_identity(authorization=f"Bearer {forged}")
        self.assertEqual(cm.exception.status_code, 401)


class TestRBAC(unittest.IsolatedAsyncioTestCase):

    def test_role_permissions(self):
        """Test that roles have expected permissions"""
        # ADMIN has everything
        self.assertTrue(RBACManager.has_permission(UserRole.ADMIN, Permission.DELETE))
        self.assertTrue(RBACManager.has_permission(UserRole.ADMIN, Permission.UPLOAD))

        # VIEWER only has ANALYZE
        self.assertTrue(RBACManager.has_permission(UserRole.VIEWER, Permission.ANALYZE))
        self.assertFalse(RBACManager.has_permission(UserRole.VIEWER, Permission.UPLOAD))

        # AUDITOR has REPORTS and AUDIT
        self.assertTrue(RBACManager.has_permission(UserRole.AUDITOR, Permission.VIEW_REPORTS))
        self.assertTrue(RBACManager.has_permission(UserRole.AUDITOR, Permission.VIEW_AUDIT))
        self.assertFalse(RBACManager.has_permission(UserRole.AUDITOR, Permission.DELETE))

        # ANALYST: real bug found live - this role was assigned to real
        # accounts (backend/main.py's auto-seeded "demo" user, and SSO
        # auto-provisioning's demo-user path) but had no UserRole member at
        # all, so every permission-gated request from an ANALYST account
        # 401'd with "Invalid role claim in token" instead of a normal
        # permission check. Same working set as LEGAL_REVIEWER.
        self.assertTrue(RBACManager.has_permission(UserRole.ANALYST, Permission.ANALYZE))
        self.assertTrue(RBACManager.has_permission(UserRole.ANALYST, Permission.UPLOAD))
        self.assertTrue(RBACManager.has_permission(UserRole.ANALYST, Permission.VIEW_REPORTS))
        self.assertFalse(RBACManager.has_permission(UserRole.ANALYST, Permission.DELETE))
        self.assertFalse(RBACManager.has_permission(UserRole.ANALYST, Permission.VIEW_AUDIT))
        self.assertFalse(RBACManager.has_permission(UserRole.ANALYST, Permission.MANAGE_USERS))

    async def test_analyst_role_claim_is_recognized_not_401(self):
        """The exact real regression: a real, signed token whose role claim
        is "ANALYST" (as issued to the auto-seeded/SSO-provisioned "demo"
        accounts) must resolve through UserRole(...) and get a normal
        permission check, not the "Invalid role claim in token" 401 every
        such account hit before this fix."""
        dependency = requires_permission(Permission.ANALYZE)
        identity = TokenIdentity(tenant_id="demo_tenant", role="ANALYST")
        result = await dependency(identity=identity)
        self.assertIs(result, identity)

    async def test_permission_dependency_allowed_returns_identity(self):
        """requires_permission's inner dependency now takes/returns a
        TokenIdentity (not a bare UserRole) - routes read tenant_id off the
        same object that was permission-checked, rather than a separate,
        unverified tenant_id parameter."""
        dependency = requires_permission(Permission.ANALYZE)
        identity = TokenIdentity(tenant_id="tenant_a", role=UserRole.VIEWER.value)
        result = await dependency(identity=identity)
        self.assertIs(result, identity)

    async def test_permission_dependency_denied(self):
        """Test requires_permission dependency when denied"""
        dependency = requires_permission(Permission.UPLOAD)
        identity = TokenIdentity(tenant_id="tenant_a", role=UserRole.VIEWER.value)
        with self.assertRaises(HTTPException) as cm:
            await dependency(identity=identity)

        self.assertEqual(cm.exception.status_code, 403)
        self.assertIn("UPLOAD", cm.exception.detail)

    async def test_permission_dependency_rejects_invalid_role_claim(self):
        """A token with a role claim that isn't a real UserRole (shouldn't
        happen via /api/auth/token's own validation, but a token could in
        principle be issued by a future different issuer) is rejected, not
        silently treated as some default role."""
        dependency = requires_permission(Permission.ANALYZE)
        identity = TokenIdentity(tenant_id="tenant_a", role="NOT_A_REAL_ROLE")
        with self.assertRaises(HTTPException) as cm:
            await dependency(identity=identity)
        self.assertEqual(cm.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
