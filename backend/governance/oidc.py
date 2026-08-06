"""
Google OIDC client - a real authorization-code + PKCE flow against
Google's actual OIDC infrastructure (its real discovery document, token
endpoint, and JWKS), not a mocked/fake IdP. See docs/CAPSTONE_SUMMARY.md's
credential-provisioning design report (this engagement) for why Google
specifically and why OIDC over a vendor-specific protocol.

JWT/JWKS verification uses `joserfc`, not `authlib.jose` - the latter is
authlib's own deprecated module (confirmed live: importing it prints
"authlib.jose module is deprecated, please use joserfc instead"). No
authlib dependency at all here: the authorization/token HTTP calls are
plain httpx against Google's real endpoints, which is simpler and more
transparent than authlib's OAuth2Client abstraction for a single, fully-
owned flow like this one.

State/PKCE: server-side, Redis-backed (backend.shared.cache.redis_cache),
short TTL (SSO_STATE_TTL_SECONDS) - a state value only this service issued
can be exchanged, and is invalidated the moment a callback consumes it.
This closes the CSRF angle a bare client-supplied state parameter would
leave open. The remaining theoretical race (two callbacks racing on the
same state before either invalidates it) is also independently closed by
Google's own token endpoint, which only ever accepts a given
authorization `code` once - a second concurrent exchange attempt gets a
real `invalid_grant` from Google regardless of anything this module does.
"""

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from backend.shared.cache.redis_cache import cache
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SSO_STATE_TTL_SECONDS = 600  # 10 minutes - generous for a real human to complete Google's consent screen


class OidcConfigError(Exception):
    """Raised when GOOGLE_OAUTH_CLIENT_ID/SECRET aren't configured."""


class OidcTokenError(Exception):
    """Raised for any callback failure: unknown/expired/reused state, a
    failed code exchange, or an ID token that doesn't verify (bad
    signature, wrong issuer/audience, expired)."""


@dataclass
class OidcIdentity:
    sub: str
    email: str
    email_verified: bool


@dataclass
class OidcCallbackResult:
    identity: OidcIdentity
    invite_token: Optional[str]


# Real HTTP calls to Google, cached in-process for the life of the worker
# (a discovery document/JWKS practically never changes) - not Redis-backed
# like the per-flow state above, since this is public, non-secret,
# non-per-request data.
_discovery_cache: dict = {}


def _get_discovery_document() -> dict:
    if "doc" not in _discovery_cache:
        response = httpx.get(GOOGLE_DISCOVERY_URL, timeout=10.0)
        response.raise_for_status()
        _discovery_cache["doc"] = response.json()
    return _discovery_cache["doc"]


def _get_jwks() -> dict:
    if "jwks" not in _discovery_cache:
        jwks_uri = _get_discovery_document()["jwks_uri"]
        response = httpx.get(jwks_uri, timeout=10.0)
        response.raise_for_status()
        _discovery_cache["jwks"] = response.json()
    return _discovery_cache["jwks"]


def _client_id() -> str:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    if not client_id:
        raise OidcConfigError("GOOGLE_OAUTH_CLIENT_ID not set")
    return client_id


def _client_secret() -> str:
    secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    if not secret:
        raise OidcConfigError("GOOGLE_OAUTH_CLIENT_SECRET not set")
    return secret


def _redirect_uri() -> str:
    return os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/oidc/callback")


def build_authorization_url(invite_token: Optional[str] = None) -> str:
    """Generates state + a PKCE verifier/challenge pair, stores them
    server-side keyed by state (Redis, SSO_STATE_TTL_SECONDS), and returns
    the real Google authorization URL to redirect the caller to. PKCE is
    used even though this is a confidential (client-secret-holding) client
    - defense in depth, and it's a request-time cost, not an
    architectural one.

    invite_token, if given, rides along in this same server-side state
    entry - NOT as a raw query param on the callback URL, since Google's
    redirect back only ever echoes `code` and `state`, nothing else this
    service originally sent. Bundling it into state is the standard,
    correct way to carry app-specific context through an OAuth redirect.
    """
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    )

    cache.set(
        f"oidc_state:{state}",
        {"code_verifier": code_verifier, "invite_token": invite_token},
        ttl=SSO_STATE_TTL_SECONDS,
    )

    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    authorization_endpoint = _get_discovery_document()["authorization_endpoint"]
    return f"{authorization_endpoint}?{urlencode(params)}"


def exchange_code_for_identity(code: str, state: str) -> "OidcCallbackResult":
    """Verifies state, exchanges the code for tokens against Google's real
    token endpoint, and verifies the returned ID token's signature against
    Google's real JWKS before trusting any claim in it. Raises
    OidcTokenError on any failure. Returns the verified identity plus
    whatever invite_token (if any) build_authorization_url was called
    with for this flow."""
    state_key = f"oidc_state:{state}"
    stored = cache.get(state_key)
    if not stored:
        raise OidcTokenError("Unknown, expired, or already-used state parameter")
    # Invalidate immediately - a replayed callback with the same state
    # must not be able to reuse this verifier, independent of whatever
    # happens with the underlying authorization code at Google's end.
    cache.set(state_key, None, ttl=1)

    code_verifier = stored["code_verifier"]
    invite_token = stored.get("invite_token")
    token_endpoint = _get_discovery_document()["token_endpoint"]

    try:
        response = httpx.post(
            token_endpoint,
            data={
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _redirect_uri(),
                "code_verifier": code_verifier,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise OidcTokenError(f"Token exchange request failed: {e}") from e

    if response.status_code >= 400:
        raise OidcTokenError(f"Token exchange rejected by Google: {response.status_code} {response.text}")

    id_token = response.json().get("id_token")
    if not id_token:
        raise OidcTokenError("Token response did not include an id_token")

    identity = _verify_id_token(id_token)
    return OidcCallbackResult(identity=identity, invite_token=invite_token)


def _verify_id_token(id_token: str) -> OidcIdentity:
    key_set = KeySet.import_key_set(_get_jwks())
    try:
        token = jwt.decode(id_token, key_set, algorithms=["RS256"])
    except JoseError as e:
        raise OidcTokenError(f"ID token signature verification failed: {e}") from e

    claims_registry = jwt.JWTClaimsRegistry(
        iss={"essential": True, "values": list(GOOGLE_ISSUERS)},
        aud={"essential": True, "value": _client_id()},
        exp={"essential": True},
    )
    try:
        claims_registry.validate(token.claims)
    except JoseError as e:
        raise OidcTokenError(f"ID token claims validation failed: {e}") from e

    claims = token.claims
    sub = claims.get("sub")
    if not sub:
        raise OidcTokenError("ID token is missing the 'sub' claim")

    return OidcIdentity(
        sub=sub,
        email=claims.get("email", ""),
        email_verified=bool(claims.get("email_verified")),
    )
