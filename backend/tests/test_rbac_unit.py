import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from fastapi import HTTPException

# Add current directory to path
sys.path.insert(0, os.getcwd())

from backend.governance.rbac import RBACManager, UserRole, Permission, requires_permission, get_current_user_role

class TestRBAC(unittest.IsolatedAsyncioTestCase):

    async def test_missing_role_header_defaults_to_viewer_not_admin(self):
        """
        Regression test: get_current_user_role previously returned
        UserRole.ADMIN when the X-User-Role header was missing, despite its
        own comment claiming it defaults to VIEWER "for safety" - meaning any
        request omitting the header silently got full permissions, including
        DELETE and MANAGE_POLICIES. It must default to VIEWER (minimal
        permissions), matching test_rbac_integrated.py's
        test_missing_role_defaults_to_viewer.
        """
        role = await get_current_user_role(x_user_role=None)
        self.assertEqual(role, UserRole.VIEWER)
        self.assertNotEqual(role, UserRole.ADMIN)

        # Confirm VIEWER's actual permission footprint stays minimal: analysis
        # allowed, destructive/administrative actions denied.
        self.assertTrue(RBACManager.has_permission(role, Permission.ANALYZE))
        self.assertFalse(RBACManager.has_permission(role, Permission.DELETE))
        self.assertFalse(RBACManager.has_permission(role, Permission.MANAGE_POLICIES))
        self.assertFalse(RBACManager.has_permission(role, Permission.UPLOAD))

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

    async def test_permission_dependency_allowed(self):
        """Test requires_permission dependency when allowed"""
        dependency = requires_permission(Permission.ANALYZE)
        # Should NOT raise exception for VIEWER
        result = await dependency(role=UserRole.VIEWER)
        self.assertTrue(result)

    async def test_permission_dependency_denied(self):
        """Test requires_permission dependency when denied"""
        dependency = requires_permission(Permission.UPLOAD)
        # Should raise 403 for VIEWER
        with self.assertRaises(HTTPException) as cm:
            await dependency(role=UserRole.VIEWER)
        
        self.assertEqual(cm.exception.status_code, 403)
        self.assertIn("UPLOAD", cm.exception.detail)

if __name__ == "__main__":
    unittest.main()
