from enum import Enum
from typing import List, Dict, Set, Optional
from fastapi import Header, HTTPException, Depends, status
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

async def get_current_user_role(x_user_role: Optional[str] = Header(None)) -> UserRole:
    """
    FastAPI dependency to extract user role from header.
    Mock implementation - in production this would validate a JWT token.
    """
    if not x_user_role:
        logger.warning("Access attempted without user role header")
        # Default to VIEWER for safety - matches test_missing_role_defaults_to_viewer
        return UserRole.VIEWER
        
    try:
        role = UserRole(x_user_role.upper())
        return role
    except ValueError:
        logger.error(f"Invalid user role provided: {x_user_role}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid user role: {x_user_role}"
        )

def requires_permission(permission: Permission):
    """
    FastAPI dependency factory for RBAC.
    Usage: @app.get("/...", dependencies=[Depends(requires_permission(Permission.UPLOAD))])
    """
    async def permission_dependency(role: UserRole = Depends(get_current_user_role)):
        if not RBACManager.has_permission(role, permission):
            logger.error(f"RBAC Denied: Role '{role}' attempted action requiring '{permission}'")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Resource requires '{permission}' permission which is not assigned to role '{role}'"
            )
        logger.info(f"RBAC Allowed: Role '{role}' authorized for '{permission}'")
        return True
        
    return permission_dependency
