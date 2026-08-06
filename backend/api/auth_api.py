"""
Registration, invites, MFA, and token issuance (governance/auth.py has the
validation-side design rationale).

Credential provisioning (this engagement's design report): self-service
POST /register is now bootstrap-only - it may create the first user of a
brand-new tenant_id, but joining an already-provisioned tenant requires a
real invite from an admin (POST /invites, GET+POST /invites/{token}...).
This is the actual fix for the vulnerability the previous, fully-open
version of this endpoint had: any unauthenticated caller could register
into ANY existing tenant_id with ADMIN and nothing stopped them. See
infrastructure/user_repository.py (TenantAlreadyProvisionedError) and
infrastructure/invite_repository.py for the mechanisms.

POST /token now returns an MFA challenge instead of a token when the
matched account has MFA enabled (infrastructure/user_repository.py's
mfa_enabled) - the real credential check still happens here, but issuance
is deferred to POST /mfa/verify. See governance/mfa.py for the TOTP
design (why pyotp, replay protection, backup codes).

api/sso_api.py (separate file) holds the OIDC redirect/callback routes -
kept apart since they're GET-based browser redirects, a different shape
from this file's JSON POST endpoints, not because the concerns are
unrelated (SSO account creation reuses this file's invite-consumption
logic directly - see sso_api.py's callback handler).
"""

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from backend.governance.auth import create_access_token, get_current_identity, DEFAULT_TOKEN_EXPIRY, TokenIdentity
from backend.governance import mfa
from backend.governance.rbac import Permission, UserRole, requires_permission
from backend.infrastructure.email_service import EmailService
from backend.infrastructure.invite_repository import InviteRepository
from backend.infrastructure.user_repository import (
    TenantAlreadyProvisionedError,
    UserRepository,
    UsernameAlreadyExistsError,
)
from backend.shared.cache.redis_cache import cache
from backend.shared.middleware.rate_limit import (
    limiter,
    AUTH_INVITE_ACCEPT_RATE_LIMIT,
    AUTH_MFA_VERIFY_RATE_LIMIT,
    AUTH_REGISTER_RATE_LIMIT,
    AUTH_TOKEN_RATE_LIMIT,
)
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_user_repository = UserRepository()
_invite_repository = InviteRepository()
_email_service = EmailService()

MFA_PENDING_TTL_SECONDS = 300  # 5 minutes to complete the second login step


# -- Registration / login -----------------------------------------------

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
    """Either a real token (mfa_required=False, access_token set) or an
    MFA challenge (mfa_required=True, mfa_token set, access_token None) -
    a caller must complete POST /mfa/verify to get a real token in the
    second case."""
    access_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: Optional[int] = None
    mfa_required: bool = False
    mfa_token: Optional[str] = None


@router.post("/register", response_model=RegisterResponse, status_code=201)
@limiter.limit(AUTH_REGISTER_RATE_LIMIT)
async def register(request: Request, payload: RegisterRequest):
    """
    Bootstrap-only: creates the first user of a brand-new tenant_id.
    Rejects (403) if tenant_id already has any members - from that point
    on, new members must be invited by an existing admin (POST /invites).
    This keeps a real, no-manual-seed-step bootstrap path for a genuinely
    new organization while closing the open-registration-into-any-tenant
    vulnerability this whole feature exists to fix.

    Rate-limited (audit finding #16, AUTH_REGISTER_RATE_LIMIT). The
    `request: Request` parameter (unused directly here) is required by
    @limiter.limit to identify the calling client; the request body is
    `payload`, not `request`, to avoid colliding with it.
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
            enforce_tenant_bootstrap=True,
        )
    except UsernameAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except TenantAlreadyProvisionedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"Registered new account '{account.username}' for tenant '{account.tenant_id}' (bootstrap)")
    return RegisterResponse(username=account.username, tenant_id=account.tenant_id, role=account.role)


@router.post("/token", response_model=TokenResponse)
@limiter.limit(AUTH_TOKEN_RATE_LIMIT)
async def issue_token(request: Request, payload: TokenRequest):
    """
    Verify real credentials against a stored, bcrypt-hashed account. If
    the account has MFA enabled, returns an MFA challenge instead of a
    token (mfa_required=True) - the caller must then complete POST
    /mfa/verify with the returned mfa_token + a TOTP/backup code before
    getting a real, usable access_token.

    Deliberately the same generic error for "no such username" and "wrong
    password" - a login failure must not reveal which username exists.
    """
    account = _user_repository.verify_credentials(payload.username, payload.password)
    if account is None:
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    totp_state = _user_repository.get_totp_state(account.username)
    if totp_state and totp_state.mfa_enabled:
        mfa_token = secrets.token_urlsafe(24)
        cache.set(f"mfa_pending:{mfa_token}", {"username": account.username}, ttl=MFA_PENDING_TTL_SECONDS)
        logger.info(f"Password verified for '{account.username}' - MFA challenge issued")
        return TokenResponse(mfa_required=True, mfa_token=mfa_token)

    token = create_access_token(tenant_id=account.tenant_id, role=account.role, username=account.username)
    logger.info(f"Issued token for user '{account.username}' (tenant '{account.tenant_id}', role '{account.role}')")
    return TokenResponse(access_token=token, expires_in=int(DEFAULT_TOKEN_EXPIRY.total_seconds()))


# -- MFA (TOTP) -----------------------------------------------------------

class MfaSetupResponse(BaseModel):
    provisioning_uri: str


class MfaConfirmRequest(BaseModel):
    code: str


class MfaConfirmResponse(BaseModel):
    backup_codes: list[str]


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str


@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(identity: TokenIdentity = Depends(get_current_identity)):
    """
    Generates a new TOTP secret and returns its otpauth:// provisioning
    URI (rendered as a QR code client-side - no server-side image
    generation dependency). mfa_enabled stays false until POST
    /mfa/confirm proves the caller actually captured it in a real
    authenticator app - see UserRepository.set_pending_totp_secret.
    """
    if not identity.username:
        raise HTTPException(status_code=400, detail="This token has no associated username - log in again")

    secret = mfa.generate_totp_secret()
    _user_repository.set_pending_totp_secret(identity.username, secret)
    return MfaSetupResponse(provisioning_uri=mfa.provisioning_uri(secret, identity.username))


@router.post("/mfa/confirm", response_model=MfaConfirmResponse)
async def mfa_confirm(payload: MfaConfirmRequest, identity: TokenIdentity = Depends(get_current_identity)):
    """
    Proves the caller captured the secret from POST /mfa/setup correctly
    (one valid code) before MFA is actually enforced on future logins.
    Issues 10 single-use backup codes at the same time - shown exactly
    once here, stored only as bcrypt hashes from this point on (module
    docstring in user_repository.py has the primitive-choice rationale).
    """
    if not identity.username:
        raise HTTPException(status_code=400, detail="This token has no associated username - log in again")

    secret = _user_repository.get_decrypted_totp_secret(identity.username)
    if not secret:
        raise HTTPException(status_code=400, detail="No pending MFA setup - call POST /mfa/setup first")

    step = mfa.verify_totp_code(secret, payload.code, last_used_step=None)
    if step is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    backup_codes = mfa.generate_backup_codes()
    _user_repository.enable_mfa(identity.username, backup_codes)
    _user_repository.record_totp_step_used(identity.username, step)
    logger.info(f"MFA enabled for '{identity.username}'")
    return MfaConfirmResponse(backup_codes=backup_codes)


@router.post("/mfa/verify", response_model=TokenResponse)
@limiter.limit(AUTH_MFA_VERIFY_RATE_LIMIT)
async def mfa_verify(request: Request, payload: MfaVerifyRequest):
    """
    Second step of a two-step MFA login: consumes the mfa_token from
    POST /token's challenge response, verifies a TOTP code (or, failing
    that, a single-use backup code), and only then issues a real,
    usable access_token. Rate-limited - the obvious brute-force target
    for a 6-digit code.
    """
    pending_key = f"mfa_pending:{payload.mfa_token}"
    pending = cache.get(pending_key)
    if not pending:
        raise HTTPException(status_code=401, detail="Unknown or expired MFA challenge")
    cache.set(pending_key, None, ttl=1)  # single-use - a replayed mfa_token must not work twice
    username = pending["username"]

    totp_state = _user_repository.get_totp_state(username)
    if not totp_state or not totp_state.mfa_enabled or not totp_state.totp_secret_encrypted:
        raise HTTPException(status_code=401, detail="MFA is not enabled for this account")

    secret = _user_repository.get_decrypted_totp_secret(username)
    step = mfa.verify_totp_code(secret, payload.code, last_used_step=totp_state.mfa_last_used_step)
    if step is not None:
        _user_repository.record_totp_step_used(username, step)
    elif _user_repository.consume_backup_code(username, payload.code):
        logger.info(f"'{username}' completed MFA login via backup code")
    else:
        raise HTTPException(status_code=401, detail="Invalid, expired, or already-used code")

    account = _user_repository.get_user_by_username(username)
    if account is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")

    token = create_access_token(tenant_id=account.tenant_id, role=account.role, username=username)
    logger.info(f"Issued token for '{username}' after MFA verification")
    return TokenResponse(access_token=token, expires_in=int(DEFAULT_TOKEN_EXPIRY.total_seconds()))


# -- Org invites ------------------------------------------------------

class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: str = Field(..., description="One of UserRole's values")


class InviteCreateResponse(BaseModel):
    email: str
    tenant_id: str
    role: str
    email_sent: bool


class InvitePreviewResponse(BaseModel):
    email: str
    tenant_id: str
    role: str


class InviteAcceptRequest(BaseModel):
    username: str = Field(..., description="3-64 characters: letters, numbers, '.', '_', '-'")
    password: str = Field(..., min_length=8, description="At least 8 characters")


@router.post("/invites", response_model=InviteCreateResponse, status_code=201)
async def create_invite(
    payload: InviteCreateRequest, identity: TokenIdentity = Depends(requires_permission(Permission.MANAGE_USERS)),
):
    """
    Admin-only (Permission.MANAGE_USERS - ADMIN has it automatically,
    RBACManager.ROLE_PERMISSIONS). Scoped to the caller's OWN tenant_id
    only, taken from the verified JWT, never from the request body - an
    admin cannot invite someone into a tenant they don't belong to.
    """
    try:
        role = UserRole(payload.role.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {payload.role}. Must be one of {[r.value for r in UserRole]}")

    invited_by = identity.username or f"tenant:{identity.tenant_id}"
    raw_token = _invite_repository.create_invite(
        email=payload.email, tenant_id=identity.tenant_id, role=role.value, invited_by=invited_by,
    )

    accept_url = f"{_frontend_base_url()}/accept-invite?token={raw_token}"
    send_result = _email_service.send_invite_email(
        to_email=payload.email, tenant_id=identity.tenant_id, role=role.value, accept_url=accept_url,
    )
    if not send_result.sent:
        logger.warning(f"Invite created for '{payload.email}' but email send failed: {send_result.reason}")

    return InviteCreateResponse(
        email=payload.email, tenant_id=identity.tenant_id, role=role.value, email_sent=send_result.sent,
    )


@router.get("/invites/{token}", response_model=InvitePreviewResponse)
async def preview_invite(token: str):
    """Public, non-consuming preview - lets the frontend show "you're
    invited to join <tenant> as <role>" before the invitee commits to a
    password. Same generic 404 for unknown/expired/already-used (see
    InviteRepository.get_invite's docstring)."""
    invite = _invite_repository.get_invite(token)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found, expired, or already used")
    return InvitePreviewResponse(email=invite.email, tenant_id=invite.tenant_id, role=invite.role)


@router.post("/invites/{token}/accept", response_model=RegisterResponse, status_code=201)
@limiter.limit(AUTH_INVITE_ACCEPT_RATE_LIMIT)
async def accept_invite(request: Request, token: str, payload: InviteAcceptRequest):
    """
    Consumes the invite (atomically, single-use - InviteRepository.
    consume_invite) and creates the account with tenant_id/role read from
    the SERVER-SIDE invite record, never from client input - this is the
    actual fix for the vulnerability self-service registration had.
    """
    invite = _invite_repository.consume_invite(token)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found, expired, or already used")

    try:
        account = _user_repository.create_user(
            username=payload.username,
            password=payload.password,
            tenant_id=invite.tenant_id,
            role=invite.role,
            email=invite.email,
        )
    except UsernameAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"Invite accepted: '{account.username}' joined tenant '{account.tenant_id}' as '{account.role}'")
    return RegisterResponse(username=account.username, tenant_id=account.tenant_id, role=account.role)


def _frontend_base_url() -> str:
    import os
    return os.getenv("FRONTEND_BASE_URL", "http://localhost:5173")
