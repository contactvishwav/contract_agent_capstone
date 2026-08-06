"""
Google OIDC SSO tests (credential-provisioning engagement, docs design
report). Two layers:

  - OidcModuleTests: governance/oidc.py in isolation - state validation,
    PKCE, and ID-token verification against a locally-generated RSA
    keypair formatted as a real JWKS document (same shape Google's real
    JWKS has, verified live against Google's actual endpoint during this
    engagement - see governance/oidc.py's module docstring). Network
    calls (_get_discovery_document/_get_jwks, the token-exchange POST)
    are mocked here so this checked-in suite runs fast and offline; the
    real, live-network path was independently verified by hand (real
    discovery document, real JWKS, a genuinely tampered/forged token
    rejected by Google's real signing keys) as part of building this
    feature, not asserted here without having been checked at all.

  - SsoApiTests: api/sso_api.py's HTTP routes, with oidc.
    exchange_code_for_identity mocked to return controlled results -
    proves the account-linking/invite-gating logic (the actual security
    boundary: SSO alone must never self-provision into a tenant).
"""

import json
import re
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.api import auth_api, sso_api
    from backend.governance import oidc

from backend.tests.test_invite_repository import FakeInviteGraph
from backend.tests.test_user_repository import FakeUserGraph

_TEST_KEY = RSAKey.generate_key(2048, parameters={"kid": "test-kid"})
_TEST_JWKS = {"keys": [_TEST_KEY.as_dict(private=False)]}
_TEST_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


def _make_id_token(
    sub="google-subject-123", email="alice@example.com", email_verified=True,
    iss="https://accounts.google.com", aud=_TEST_CLIENT_ID, exp_delta=300, kid="test-kid",
) -> str:
    claims = {
        "sub": sub, "email": email, "email_verified": email_verified,
        "iss": iss, "aud": aud, "exp": int(time.time()) + exp_delta,
    }
    return joserfc_jwt.encode({"alg": "RS256", "kid": kid}, claims, _TEST_KEY)


def _extract_session_from_bridge_html(html_body: str) -> dict:
    """
    A successful GET /callback now renders a real HTML page (api/sso_api.
    py's _session_bridge_html) instead of JSON - it writes the session
    into localStorage via an inline `localStorage.setItem(key, "<escaped
    JSON>")` call and redirects into the SPA, rather than returning
    {access_token: ...} for a caller to read directly. Extracts the real
    session dict from that embedded call so tests can still assert on the
    real token/tenant_id/role, the same way a browser's JS engine would
    unescape it.
    """
    match = re.search(r'setItem\("contract_intelligence_auth", (".*?")\);', html_body)
    assert match, f"session bridge script not found in response body: {html_body[:500]}"
    inner_json_text = json.loads(match.group(1))  # un-escapes the JS string literal
    return json.loads(inner_json_text)  # parses the actual session JSON


class OidcModuleTests(unittest.TestCase):
    def setUp(self):
        oidc._discovery_cache.clear()
        oidc._discovery_cache["doc"] = {
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        }
        oidc._discovery_cache["jwks"] = _TEST_JWKS
        self.addCleanup(oidc._discovery_cache.clear)

        self._env_patcher = patch.dict(
            "os.environ",
            {
                "GOOGLE_OAUTH_CLIENT_ID": _TEST_CLIENT_ID,
                "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
                "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8000/api/auth/oidc/callback",
            },
        )
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)

    def test_build_authorization_url_points_at_googles_real_endpoint(self):
        url = oidc.build_authorization_url()
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertIn("client_id=", url)
        self.assertIn("code_challenge=", url)
        self.assertIn("code_challenge_method=S256", url)

    def test_verify_id_token_accepts_a_genuinely_valid_token(self):
        token = _make_id_token()
        identity = oidc._verify_id_token(token)
        self.assertEqual(identity.sub, "google-subject-123")
        self.assertEqual(identity.email, "alice@example.com")
        self.assertTrue(identity.email_verified)

    def test_verify_id_token_rejects_garbage(self):
        with self.assertRaises(oidc.OidcTokenError):
            oidc._verify_id_token("not.a.real.token")

    def test_verify_id_token_rejects_wrong_signing_key(self):
        """The core forgery case: a token signed by a DIFFERENT key than
        the one in Google's (here, the test) JWKS must be rejected -
        proves signature verification actually checks the signature, not
        just the token's shape."""
        forged_key = RSAKey.generate_key(2048, parameters={"kid": "test-kid"})
        forged_token = joserfc_jwt.encode(
            {"alg": "RS256", "kid": "test-kid"},
            {"sub": "attacker", "iss": "https://accounts.google.com", "aud": _TEST_CLIENT_ID, "exp": int(time.time()) + 300},
            forged_key,
        )
        with self.assertRaises(oidc.OidcTokenError):
            oidc._verify_id_token(forged_token)

    def test_verify_id_token_rejects_wrong_issuer(self):
        token = _make_id_token(iss="https://evil.example.com")
        with self.assertRaises(oidc.OidcTokenError):
            oidc._verify_id_token(token)

    def test_verify_id_token_rejects_wrong_audience(self):
        token = _make_id_token(aud="some-other-client-id")
        with self.assertRaises(oidc.OidcTokenError):
            oidc._verify_id_token(token)

    def test_verify_id_token_rejects_expired_token(self):
        token = _make_id_token(exp_delta=-300)
        with self.assertRaises(oidc.OidcTokenError):
            oidc._verify_id_token(token)

    def test_exchange_code_for_identity_rejects_unknown_state(self):
        with self.assertRaises(oidc.OidcTokenError):
            oidc.exchange_code_for_identity("some-code", "state-nobody-issued")

    def test_exchange_code_for_identity_rejects_a_replayed_state(self):
        url = oidc.build_authorization_url()
        state = url.split("state=")[1].split("&")[0]

        token_response = MagicMock(status_code=200, json=lambda: {"id_token": _make_id_token()})
        with patch("backend.governance.oidc.httpx.post", return_value=token_response):
            first = oidc.exchange_code_for_identity("real-code", state)
            self.assertEqual(first.identity.sub, "google-subject-123")

            with self.assertRaises(oidc.OidcTokenError):
                oidc.exchange_code_for_identity("real-code", state)

    def test_exchange_code_for_identity_carries_the_invite_token_through(self):
        url = oidc.build_authorization_url(invite_token="abc123invite")
        state = url.split("state=")[1].split("&")[0]

        token_response = MagicMock(status_code=200, json=lambda: {"id_token": _make_id_token()})
        with patch("backend.governance.oidc.httpx.post", return_value=token_response):
            result = oidc.exchange_code_for_identity("real-code", state)

        self.assertEqual(result.invite_token, "abc123invite")

    def test_exchange_code_for_identity_raises_when_google_rejects_the_code(self):
        url = oidc.build_authorization_url()
        state = url.split("state=")[1].split("&")[0]

        error_response = MagicMock(status_code=400, text='{"error": "invalid_grant"}')
        with patch("backend.governance.oidc.httpx.post", return_value=error_response):
            with self.assertRaises(oidc.OidcTokenError):
                oidc.exchange_code_for_identity("bad-code", state)


class SsoApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._user_patcher = patch.object(sso_api._user_repository, "graph", FakeUserGraph())
        self._user_patcher.start()
        self.addCleanup(self._user_patcher.stop)
        self._invite_patcher = patch.object(sso_api._invite_repository, "graph", FakeInviteGraph())
        self._invite_patcher.start()
        self.addCleanup(self._invite_patcher.stop)

    def _mock_callback_result(self, sub="google-sub-1", email="alice@example.com", email_verified=True, invite_token=None):
        return oidc.OidcCallbackResult(
            identity=oidc.OidcIdentity(sub=sub, email=email, email_verified=email_verified),
            invite_token=invite_token,
        )

    def test_login_redirects_to_a_real_authorization_url(self):
        with patch.object(oidc, "build_authorization_url", return_value="https://accounts.google.com/o/oauth2/v2/auth?mocked=1"):
            response = self.client.get("/api/auth/oidc/login", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://accounts.google.com/o/oauth2/v2/auth?mocked=1")

    def test_callback_with_unverified_email_is_rejected(self):
        with patch.object(sso_api.oidc, "exchange_code_for_identity", return_value=self._mock_callback_result(email_verified=False)):
            response = self.client.get("/api/auth/oidc/callback?code=x&state=y")
        self.assertEqual(response.status_code, 401)

    def test_callback_with_no_invite_and_no_existing_account_is_rejected(self):
        """The core security property: a real, verified Google identity
        alone must never be enough to self-provision into a tenant."""
        with patch.object(sso_api.oidc, "exchange_code_for_identity", return_value=self._mock_callback_result()):
            response = self.client.get("/api/auth/oidc/callback?code=x&state=y")
        self.assertEqual(response.status_code, 403)

    def test_callback_with_a_valid_invite_creates_an_account_and_issues_a_token(self):
        raw_invite_token = sso_api._invite_repository.create_invite(
            email="alice@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )
        callback_result = self._mock_callback_result(email="alice@example.com", invite_token=raw_invite_token)

        with patch.object(sso_api.oidc, "exchange_code_for_identity", return_value=callback_result):
            response = self.client.get("/api/auth/oidc/callback?code=x&state=y")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        session = _extract_session_from_bridge_html(response.text)
        self.assertTrue(session["token"])
        self.assertEqual(session["tenantId"], "tenant_a")
        self.assertEqual(session["role"], "VIEWER")

        # The embedded token isn't just present - it's a real, independently
        # verifiable JWT this backend itself issued, decodable the exact
        # same way any other authenticated route validates one.
        import asyncio
        from backend.governance.auth import get_current_identity
        identity = asyncio.run(get_current_identity(authorization=f"Bearer {session['token']}"))
        self.assertEqual(identity.tenant_id, "tenant_a")
        self.assertEqual(identity.role, "VIEWER")

        linked = sso_api._user_repository.get_user_by_sso("google", "google-sub-1")
        self.assertIsNotNone(linked)
        self.assertEqual(linked.tenant_id, "tenant_a")
        self.assertEqual(linked.role, "VIEWER")

    def test_callback_rejects_invite_whose_email_does_not_match_the_google_account(self):
        raw_invite_token = sso_api._invite_repository.create_invite(
            email="someone-else@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )
        callback_result = self._mock_callback_result(email="alice@example.com", invite_token=raw_invite_token)

        with patch.object(sso_api.oidc, "exchange_code_for_identity", return_value=callback_result):
            response = self.client.get("/api/auth/oidc/callback?code=x&state=y")

        self.assertEqual(response.status_code, 403)

    def test_callback_rejects_an_already_used_invite(self):
        raw_invite_token = sso_api._invite_repository.create_invite(
            email="alice@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_x",
        )
        sso_api._invite_repository.consume_invite(raw_invite_token)  # pre-consume it
        callback_result = self._mock_callback_result(email="alice@example.com", invite_token=raw_invite_token)

        with patch.object(sso_api.oidc, "exchange_code_for_identity", return_value=callback_result):
            response = self.client.get("/api/auth/oidc/callback?code=x&state=y")

        self.assertEqual(response.status_code, 404)

    def test_callback_for_an_already_linked_identity_just_logs_in_no_invite_needed(self):
        sso_api._user_repository.create_sso_user(
            username="alice.g", tenant_id="tenant_a", role="ADMIN",
            email="alice@example.com", sso_provider="google", sso_subject="google-sub-1",
        )
        callback_result = self._mock_callback_result(sub="google-sub-1", invite_token=None)

        with patch.object(sso_api.oidc, "exchange_code_for_identity", return_value=callback_result):
            response = self.client.get("/api/auth/oidc/callback?code=x&state=y")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        session = _extract_session_from_bridge_html(response.text)
        self.assertTrue(session["token"])
        self.assertEqual(session["tenantId"], "tenant_a")
        self.assertEqual(session["role"], "ADMIN")

    def test_callback_raises_appropriately_on_token_error(self):
        with patch.object(sso_api.oidc, "exchange_code_for_identity", side_effect=oidc.OidcTokenError("bad state")):
            response = self.client.get("/api/auth/oidc/callback?code=x&state=y")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
