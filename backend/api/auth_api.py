"""
Registration + token issuance (governance/auth.py has the validation-side
design rationale, which this file does not change at all).

POST /api/auth/token now requires real credentials, verified against a
real, bcrypt-hashed account (infrastructure/user_repository.py) - it no
longer signs whatever tenant_id/role a caller hands it. POST
/api/auth/register is the (necessarily minimal, since no admin-
provisioning UI exists yet) way an account gets created in the first
place - self-service registration, not an admin-only endpoint, because
there is currently no other way for anyone to create one.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.governance.auth import create_access_token, DEFAULT_TOKEN_EXPIRY
from backend.governance.rbac import UserRole
from backend.infrastructure.user_repository import UserRepository, UsernameAlreadyExistsError
from backend.shared.middleware.rate_limit import limiter, AUTH_REGISTER_RATE_LIMIT, AUTH_TOKEN_RATE_LIMIT
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_user_repository = UserRepository()


class RegisterRequest(BaseModel):
    username: str = Field(..., description="3-64 characters: letters, numbers, '.', '_', '-'")
    password: str = Field(..., min_length=8, description="At least 8 characters")
    tenant_id: str = Field(..., description="Tenant this account belongs to")
    role: str = Field(..., description="One of UserRole's values")


class RegisterResponse(BaseModel):
    username: str
    tenant_id: str
    role: str


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


@router.post("/register", response_model=RegisterResponse, status_code=201)
@limiter.limit(AUTH_REGISTER_RATE_LIMIT)
async def register(request: Request, payload: RegisterRequest):
    """
    Create a real account. Minimal and self-service on purpose: no
    admin-provisioning UI exists in this system yet, so this is currently
    the only way any account gets created at all - there's no hidden seed
    step behind the scenes. A real deployment would gate this behind
    org-admin invites or an IdP's own signup flow; noted as a follow-up,
    not pretended away.

    Rate-limited (audit finding #16, AUTH_REGISTER_RATE_LIMIT) - the
    obvious registration-spam target given this is unauthenticated by
    necessity. The `request: Request` parameter (unused directly here) is
    required by @limiter.limit to identify the calling client; the
    request body is `payload`, not `request`, to avoid colliding with it.
    """
    try:
        role = UserRole(payload.role.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {payload.role}. Must be one of {[r.value for r in UserRole]}")

    try:
        account = _user_repository.create_user(
            username=payload.username,
            password=payload.password,
            tenant_id=payload.tenant_id,
            role=role.value,
        )
    except UsernameAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"Registered new account '{account.username}' for tenant '{account.tenant_id}'")
    return RegisterResponse(username=account.username, tenant_id=account.tenant_id, role=account.role)


@router.post("/token", response_model=TokenResponse)
@limiter.limit(AUTH_TOKEN_RATE_LIMIT)
async def issue_token(request: Request, payload: TokenRequest):
    """
    Verify real credentials against a stored, bcrypt-hashed account and,
    only on a match, issue a signed JWT carrying that account's tenant_id
    + role as claims. Replaces the previous claims-only version of this
    endpoint (which signed whatever tenant_id/role it was asked to) - this
    is the real-credential-verification step that endpoint's own honest
    limitation always pointed at as the next step.

    Deliberately the same generic error for "no such username" and "wrong
    password" - a login failure must not reveal which username exists.

    Rate-limited (audit finding #16, AUTH_TOKEN_RATE_LIMIT) - the obvious
    brute-force target given this is unauthenticated by necessity. See
    register()'s docstring for why the body param is `payload`, not
    `request`.
    """
    account = _user_repository.verify_credentials(payload.username, payload.password)
    if account is None:
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    token = create_access_token(tenant_id=account.tenant_id, role=account.role)
    logger.info(f"Issued token for user '{account.username}' (tenant '{account.tenant_id}', role '{account.role}')")

    return TokenResponse(access_token=token, expires_in=int(DEFAULT_TOKEN_EXPIRY.total_seconds()))
