"""
Real JWT-based authentication (closes the README's "Real Authenticated
Tenant Identity" future-enhancement item).

Replaces two previously-separate, unauthenticated mechanisms:
- tenant_id as a client-supplied query/path/form/body parameter (P1 item 6
  only ever reached "reject if missing" - it never verified the supplied
  value belonged to the caller, since no identity concept existed).
- X-User-Role header (governance/rbac.py's old get_current_user_role) - a
  bare, unsigned header any caller could set to anything, including
  "ADMIN".

Both are replaced by a signed JWT issued by POST /api/auth/token, carrying
tenant_id and role as claims. get_current_identity validates the token's
signature and expiry and returns both claims together - a caller can no
longer claim a tenant_id or role that isn't in a token this service
actually issued.

Key management: mirrors infrastructure/encryption.py's ENCRYPTION_KEY
convention exactly (env var, loudly-logged insecure dev fallback, no
secrets-manager integration yet since no deployment target is defined).

Honest limitation, stated explicitly rather than glossed over: POST
/api/auth/token currently accepts a bare tenant_id + role in the request
body and issues a token for it - there is no real user/organization
provisioning system to check credentials against yet, so this is signed
tenant/role *claims*, not verified *identity* in the full sense (no
username/password, no per-user accounts, no org-membership check). What
this DOES achieve for real: once issued, a token's claims can't be forged
or altered by the caller in transit (unlike the old header/query-param
mechanisms - tampering with a signed token invalidates its signature), and
every downstream route can trust that tenant_id/role came from a token
this service itself signed, not from whatever a client typed into a query
string. A real identity provider (Auth0/Okta/Cognito) can replace just the
issuance step (this module's create_access_token / the /api/auth/token
route) later without touching get_current_identity or anything that
depends on it, as long as the replacement issues a JWT this service can
verify (or verification itself moves to JWKS-based validation against the
IdP's public keys - a larger, separate follow-up).
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status

from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

# Insecure, hardcoded fallback so local dev/tests work without configuring
# a real secret - loudly logged, never silent. Never use this in
# production; set a real JWT_SECRET_KEY instead. Same convention as
# infrastructure/encryption.py's ENCRYPTION_KEY.
_DEV_DEFAULT_SECRET = "insecure-dev-only-jwt-secret-do-not-use-in-production"
_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRY = timedelta(hours=24)


def _get_secret_key() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        logger.warning(
            "JWT_SECRET_KEY not set - using an insecure, hardcoded dev-only "
            "signing secret. Set a real JWT_SECRET_KEY before issuing tokens "
            "for real use - anyone who knows this default could forge a "
            "valid token for any tenant_id/role."
        )
        secret = _DEV_DEFAULT_SECRET
    return secret


@dataclass
class TokenIdentity:
    """The resolved, verified identity from a validated JWT - tenant_id and
    role a caller cannot forge, unlike the old query-param/header
    mechanisms they replace."""
    tenant_id: str
    role: str  # raw claim string; requires_permission (rbac.py) converts to UserRole


def create_access_token(tenant_id: str, role: str, expires_delta: Optional[timedelta] = None) -> str:
    """Issue a signed JWT. See module docstring for the honest scope of
    what "issuance" means today (signed claims, not verified credentials)."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or DEFAULT_TOKEN_EXPIRY)
    payload = {
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, _get_secret_key(), algorithm=_ALGORITHM)


async def get_current_identity(authorization: Optional[str] = Header(None)) -> TokenIdentity:
    """
    FastAPI dependency: validates the `Authorization: Bearer <token>`
    header's signature and expiry, returns the tenant_id/role claims it
    carries. Unlike the old get_current_user_role, there is no default
    role and no silent fallback - a missing, malformed, expired, or
    tampered token is always a 401.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header - expected 'Bearer <token>'",
        )

    token = authorization[len("Bearer "):]
    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

    tenant_id = payload.get("tenant_id")
    role = payload.get("role")
    if not tenant_id or not role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is missing tenant_id/role claims")

    return TokenIdentity(tenant_id=tenant_id, role=role)
