from enum import Enum
from typing import List, Dict, Set, Optional
from fastapi import HTTPException, Depends, status
from backend.governance.auth import get_current_identity, TokenIdentity
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

class UserRole(str, Enum):
    ADMIN = "ADMIN"
    LEGAL_REVIEWER = "LEGAL_REVIEWER"
    AUDITOR = "AUDITOR"
    VIEWER = "VIEWER"

class Permission(str, Enum):
    UPLOAD = "UPLOAD"
    DELETE = "DELETE"
    ANALYZE = "ANALYZE"
    VIEW_REPORTS = "VIEW_REPORTS"
    MANAGE_POLICIES = "MANAGE_POLICIES"
    VIEW_AUDIT = "VIEW_AUDIT"
    MANAGE_USERS = "MANAGE_USERS"  # invite/provision accounts into one's own tenant

class RBACManager:
    """
    Manager for Role-Based Access Control.
    Maps roles to permissions and provides validation logic.
    """
    
    # Role-Permission Mapping (Static for now, could be loaded from DB/Config)
    ROLE_PERMISSIONS: Dict[UserRole, Set[Permission]] = {
        UserRole.ADMIN: set(Permission),  # Admins have all permissions
        UserRole.LEGAL_REVIEWER: {
            Permission.ANALYZE,
            Permission.UPLOAD,
            Permission.VIEW_REPORTS
        },
        UserRole.AUDITOR: {
            Permission.VIEW_REPORTS,
            Permission.VIEW_AUDIT
        },
        UserRole.VIEWER: {
            Permission.ANALYZE  # Can query/analyze but not upload/delete
        }
    }

    @classmethod
    def has_permission(cls, role: UserRole, permission: Permission) -> bool:
        """Check if a role has a specific permission"""
        allowed_permissions = cls.ROLE_PERMISSIONS.get(role, set())
        return permission in allowed_permissions

def requires_permission(permission: Permission):
    """
    FastAPI dependency factory for RBAC - now resolves role (and tenant_id)
    from a validated JWT (governance/auth.py) instead of a bare, unsigned
    X-User-Role header. Returns the resolved TokenIdentity (not just True)
    so a route can do:

        identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE))
        ... use identity.tenant_id ...

    collapsing what used to be two separate concerns per route (permission
    check via this dependency, tenant_id via a separate `Query(...)`
    parameter) into the one verified source of truth for both.

    Usage as a gate only (no route body access to identity needed):
        dependencies=[Depends(requires_permission(Permission.UPLOAD))]
    """
    async def permission_dependency(identity: TokenIdentity = Depends(get_current_identity)) -> TokenIdentity:
        try:
            role = UserRole(identity.role.upper())
        except ValueError:
            logger.error(f"Invalid role claim in token: {identity.role}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid role claim in token: {identity.role}"
            )

        if not RBACManager.has_permission(role, permission):
            logger.error(f"RBAC Denied: Role '{role}' (tenant '{identity.tenant_id}') attempted action requiring '{permission}'")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Resource requires '{permission}' permission which is not assigned to role '{role}'"
            )
        logger.info(f"RBAC Allowed: Role '{role}' (tenant '{identity.tenant_id}') authorized for '{permission}'")
        return identity

    return permission_dependency


def requires_role(required_role: UserRole):
    """
    FastAPI dependency factory gating on an exact role, not a permission -
    for actions that must stay ADMIN-only by identity, not incidentally
    (a Permission ADMIN happens to be the sole holder of today could be
    granted to another role later without anyone touching this route,
    silently widening access). Phase 4's human-review approve/reject
    endpoints are the first user: approving a HIGH/CRITICAL-risk contract
    is an ADMIN action specifically, same rationale as MANAGE_USERS.
    """
    async def role_dependency(identity: TokenIdentity = Depends(get_current_identity)) -> TokenIdentity:
        try:
            role = UserRole(identity.role.upper())
        except ValueError:
            logger.error(f"Invalid role claim in token: {identity.role}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid role claim in token: {identity.role}"
            )

        if role != required_role:
            logger.error(f"RBAC Denied: Role '{role}' (tenant '{identity.tenant_id}') attempted an action requiring role '{required_role}'")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Resource requires role '{required_role}'",
            )
        logger.info(f"RBAC Allowed: Role '{role}' (tenant '{identity.tenant_id}') authorized as '{required_role}'")
        return identity

    return role_dependency
