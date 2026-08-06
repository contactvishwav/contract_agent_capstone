"""
Google OIDC SSO routes - GET-based browser redirects, kept apart from
auth_api.py's JSON POST endpoints since callers here are a real browser's
address bar / redirect chain, not fetch()/axios JSON calls.

Account linking model (docs design report, this engagement): SSO alone
never self-provisions an account into a tenant. A first-time Google login
only succeeds if the returned email matches a live, unconsumed invite
(infrastructure/invite_repository.py) - that invite's tenant_id/role
creates the account, linked to (sso_provider="google", sso_subject=sub).
A later login from the same Google identity just issues a token. No
invite and no existing link -> 403, by design: a real Google account by
itself must never be enough to join an existing tenant.

invite_token flows through governance/oidc.py's server-side `state`
storage (GET /login accepts it as a query param, since this service
controls that URL; GET /callback receives it back from
exchange_code_for_identity, NOT as a raw callback query param - Google's
redirect only ever echoes `code`+`state`, nothing else. See oidc.py's
build_authorization_url docstring for why.
"""

import html
import json
import os
import re
import secrets as _secrets
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.governance import oidc
from backend.governance.auth import create_access_token, DEFAULT_TOKEN_EXPIRY
from backend.infrastructure.invite_repository import InviteRepository
from backend.infrastructure.user_repository import UserRepository, UsernameAlreadyExistsError
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth/oidc", tags=["auth", "sso"])
_user_repository = UserRepository()
_invite_repository = InviteRepository()

SSO_PROVIDER_GOOGLE = "google"
_USERNAME_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_.-]")

# Must match authStore.ts's STORAGE_KEY exactly - there is no shared
# constant between this backend template and the frontend bundle, so both
# sides comment the coupling explicitly (see authStore.ts's own comment
# pointing back here).
_SESSION_STORAGE_KEY = "contract_intelligence_auth"


def _session_bridge_html(token: str, tenant_id: str, role: str) -> HTMLResponse:
    """
    Google's redirect lands the real browser on THIS backend route (it has
    to - that's the exact URL registered as the OAuth client's redirect
    URI). Previously this returned raw JSON, which is correct for an API
    client but useless for a real user's browser: nothing established a
    session in the SPA, so "successfully" completing Google's consent
    screen just showed a JSON blob instead of logging the user in.

    Fix: render a real (tiny) HTML page instead of JSON. It writes the
    session directly into localStorage under authStore.ts's exact
    STORAGE_KEY/shape - computed server-side (tenant_id/role/expiresAt are
    already known here, no need to re-decode the JWT client-side) - then
    does a real top-level navigation into the SPA, which picks the session
    up automatically via authStore.ts's own loadFromStorage() at module
    load. `html.escape`/`json.dumps` on every embedded value since this is
    real HTML being templated, not an f-string into trusted markup.
    """
    session_json = json.dumps({
        "token": token,
        "tenantId": tenant_id,
        "role": role,
        "expiresAt": int((time.time() + DEFAULT_TOKEN_EXPIRY.total_seconds()) * 1000),
    })
    frontend_url = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173")
    safe_frontend_url = html.escape(frontend_url, quote=True)
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Signing you in...</title></head>
<body>
<p>Signing you in...</p>
<script>
  localStorage.setItem({json.dumps(_SESSION_STORAGE_KEY)}, {json.dumps(session_json)});
  window.location.href = "{safe_frontend_url}";
</script>
<noscript>JavaScript is required to complete sign-in. <a href="{safe_frontend_url}">Continue to the app</a>.</noscript>
</body></html>"""
    return HTMLResponse(content=body)


@router.get("/login")
async def oidc_login(invite_token: str = Query(None)):
    """
    Redirects to Google's real authorization endpoint
    (governance/oidc.py). invite_token, if the caller came from an invite
    accept page choosing "Sign in with Google" instead of setting a
    password, is threaded through server-side state so the callback below
    can use it once Google redirects back.
    """
    try:
        url = oidc.build_authorization_url(invite_token=invite_token)
    except oidc.OidcConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return RedirectResponse(url)


@router.get("/callback")
async def oidc_callback(code: str = Query(...), state: str = Query(...)):
    """
    Google redirects here with `code`+`state`. Exchanges + verifies the ID
    token against Google's real infrastructure, then:
      - if an account is already linked to this Google identity -> issue a token.
      - else, if this flow carried an invite_token that is a live,
        unconsumed invite whose email matches the verified Google email
        -> consume the invite, create the account, issue a token.
      - else -> 403 (SSO alone cannot self-provision into a tenant).
    """
    try:
        result = oidc.exchange_code_for_identity(code, state)
    except oidc.OidcTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except oidc.OidcConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))

    identity = result.identity
    if not identity.email_verified:
        raise HTTPException(status_code=401, detail="Google account email is not verified")

    existing = _user_repository.get_user_by_sso(SSO_PROVIDER_GOOGLE, identity.sub)
    if existing is not None:
        token = create_access_token(tenant_id=existing.tenant_id, role=existing.role, username=existing.username)
        logger.info(f"SSO login for existing account '{existing.username}' via Google")
        return _session_bridge_html(token, existing.tenant_id, existing.role)

    if not result.invite_token:
        raise HTTPException(
            status_code=403, detail="No account linked to this Google identity and no invite was used to sign in"
        )

    invite = _invite_repository.consume_invite(result.invite_token)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found, expired, or already used")
    if invite.email.lower() != identity.email.lower():
        raise HTTPException(status_code=403, detail="Invite email does not match the Google account used to sign in")

    account = _create_sso_account_with_unique_username(invite, identity)

    token = create_access_token(tenant_id=account.tenant_id, role=account.role, username=account.username)
    logger.info(f"SSO account created: '{account.username}' joined tenant '{account.tenant_id}' via Google invite")
    return _session_bridge_html(token, account.tenant_id, account.role)


def _create_sso_account_with_unique_username(invite, identity: "oidc.OidcIdentity"):
    username = _username_from_email(identity.email)
    try:
        return _user_repository.create_sso_user(
            username=username, tenant_id=invite.tenant_id, role=invite.role,
            email=identity.email, sso_provider=SSO_PROVIDER_GOOGLE, sso_subject=identity.sub,
        )
    except UsernameAlreadyExistsError:
        # Rare (email-local-part collision across tenants/users) but real -
        # a random suffix keeps signup working rather than failing outright.
        return _user_repository.create_sso_user(
            username=f"{username}.{_secrets.token_hex(3)}", tenant_id=invite.tenant_id, role=invite.role,
            email=identity.email, sso_provider=SSO_PROVIDER_GOOGLE, sso_subject=identity.sub,
        )


def _username_from_email(email: str) -> str:
    """UserRepository's username pattern is [a-zA-Z0-9_.-]{3,64} - an
    email's local part can contain '+' and other characters that pattern
    rejects, and can be shorter than the 3-character minimum."""
    local_part = email.split("@", 1)[0]
    cleaned = _USERNAME_INVALID_CHARS.sub("", local_part) or "user"
    if len(cleaned) < 3:
        cleaned = (cleaned + "000")[:3]
    return cleaned[:64]
