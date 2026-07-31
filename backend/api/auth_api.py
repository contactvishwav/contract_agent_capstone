"""
Token issuance endpoint (governance/auth.py has the full design rationale
and honest limitations of what "issuance" means today).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.governance.auth import create_access_token, DEFAULT_TOKEN_EXPIRY
from backend.governance.rbac import UserRole
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenRequest(BaseModel):
    tenant_id: str = Field(..., description="Tenant to scope the issued token to")
    role: str = Field(..., description="Role to embed in the token - one of UserRole's values")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


@router.post("/token", response_model=TokenResponse)
async def issue_token(request: TokenRequest):
    """
    Issue a signed JWT carrying tenant_id + role as claims.

    Honest scope: this endpoint has no credentials to check yet (no
    username/password, no per-user accounts, no org-membership
    verification) - it signs whatever tenant_id/role it's asked to, the
    same way a real identity provider's token endpoint would after it
    itself verified a login. What changes from before: once issued, the
    resulting token's claims are tamper-evident (any downstream route can
    trust them), which the old client-supplied tenant_id query param and
    X-User-Role header never were. Swap this endpoint for a real IdP
    (Auth0/Okta/Cognito) later without touching validation
    (governance/auth.get_current_identity) or any route that depends on it.
    """
    try:
        role = UserRole(request.role.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {request.role}. Must be one of {[r.value for r in UserRole]}")

    if not request.tenant_id.strip():
        raise HTTPException(status_code=400, detail="tenant_id must not be empty")

    token = create_access_token(tenant_id=request.tenant_id, role=role.value)
    logger.info(f"Issued token for tenant '{request.tenant_id}', role '{role.value}'")

    return TokenResponse(access_token=token, expires_in=int(DEFAULT_TOKEN_EXPIRY.total_seconds()))
