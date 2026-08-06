"""
HTTP-level tests for TOTP MFA (credential-provisioning engagement, docs
design report): setup/confirm/verify, the two-step login flow, and the
named adversarial cases - replayed TOTP codes, wrong codes, and backup-
code single-use.

Uses real pyotp arithmetic against the real secret returned by /mfa/setup
(parsed out of the returned otpauth:// URI) - not a stubbed TOTP. "Moving
to a fresh 30s window" is done via a patched, explicitly-advanced clock
(self.now, patched into governance/mfa.py's time.time - the only place
this module's verify_totp_code reads the current time from) rather than
a real time.sleep(30) - deterministic and fast, not dependent on wall-
clock timing. pyotp.TOTP.now() itself reads datetime.datetime.now(), not
time.time(), so test-side codes are generated via .at(explicit_timestamp)
instead of .now(), keeping both sides driven by the same single fake
clock (self.now) rather than two different, easy-to-desync mechanisms.
"""

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import bcrypt
import pyotp
from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.api import auth_api

from backend.tests.test_user_repository import FakeUserGraph


def _extract_secret(provisioning_uri: str) -> str:
    query = parse_qs(urlparse(provisioning_uri).query)
    return query["secret"][0]


class TotpMfaApiTests(unittest.TestCase):
    FAKE_NOW = 1_700_000_000.0  # arbitrary fixed epoch - deterministic across runs

    def setUp(self):
        self.now = self.FAKE_NOW
        self._time_patcher = patch("backend.governance.mfa.time.time", side_effect=lambda: self.now)
        self._time_patcher.start()
        self.addCleanup(self._time_patcher.stop)

        # Real bcrypt, just at a much lower (still-real, still-correct)
        # cost factor - this file's enable_mfa calls hash 10 backup codes
        # per test on top of the usual password hash, and bcrypt's
        # intentionally-slow default cost (~12 rounds) turns that into a
        # multi-minute test file for no correctness benefit in a suite
        # that never depends on production-strength cost here.
        _real_gensalt = bcrypt.gensalt  # captured before patching, to avoid the lambda calling its own mock
        self._gensalt_patcher = patch("bcrypt.gensalt", side_effect=lambda: _real_gensalt(rounds=4))
        self._gensalt_patcher.start()
        self.addCleanup(self._gensalt_patcher.stop)

        self.client = TestClient(app)
        self._graph_patcher = patch.object(auth_api._user_repository, "graph", FakeUserGraph())
        self._graph_patcher.start()
        self.addCleanup(self._graph_patcher.stop)

        register = self.client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "s3cret-password!", "tenant_id": "tenant_a", "role": "ADMIN"},
        )
        assert register.status_code == 201
        login = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        assert login.status_code == 200
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def _code_at(self, secret: str) -> str:
        return pyotp.TOTP(secret).at(int(self.now))

    def _advance_to_next_totp_window(self) -> None:
        self.now += pyotp.TOTP("A" * 16).interval  # default interval (30s); any secret has the same interval here

    def _setup_and_confirm_mfa(self):
        setup = self.client.post("/api/auth/mfa/setup", headers=self.headers)
        secret = _extract_secret(setup.json()["provisioning_uri"])
        confirm = self.client.post("/api/auth/mfa/confirm", json={"code": self._code_at(secret)}, headers=self.headers)
        return secret, confirm.json()["backup_codes"]

    # -- setup / confirm --------------------------------------------------

    def test_mfa_setup_returns_a_real_otpauth_uri(self):
        response = self.client.post("/api/auth/mfa/setup", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["provisioning_uri"].startswith("otpauth://totp/"))

    def test_mfa_setup_requires_authentication(self):
        response = self.client.post("/api/auth/mfa/setup")
        self.assertEqual(response.status_code, 401)

    def test_mfa_confirm_with_correct_code_enables_mfa_and_returns_backup_codes(self):
        secret, backup_codes = self._setup_and_confirm_mfa()
        self.assertEqual(len(backup_codes), 10)
        self.assertEqual(len(set(backup_codes)), 10, "backup codes must be distinct")

    def test_mfa_confirm_with_wrong_code_is_rejected_and_does_not_enable_mfa(self):
        self.client.post("/api/auth/mfa/setup", headers=self.headers)
        response = self.client.post("/api/auth/mfa/confirm", json={"code": "000000"}, headers=self.headers)
        self.assertEqual(response.status_code, 401)

        # MFA must still be off - a login right after must NOT require it.
        login = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        self.assertFalse(login.json()["mfa_required"])

    def test_mfa_confirm_without_a_pending_setup_is_rejected(self):
        response = self.client.post("/api/auth/mfa/confirm", json={"code": "123456"}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    # -- two-step login ------------------------------------------------

    def test_login_requires_mfa_once_enabled(self):
        self._setup_and_confirm_mfa()

        response = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["mfa_required"])
        self.assertIsNone(response.json()["access_token"])
        self.assertTrue(response.json()["mfa_token"])

    def test_wrong_password_still_rejected_even_with_mfa_enabled(self):
        """MFA is a second factor, not a password bypass - a wrong
        password must still fail at the first step regardless."""
        self._setup_and_confirm_mfa()
        response = self.client.post("/api/auth/token", json={"username": "alice", "password": "wrong-password"})
        self.assertEqual(response.status_code, 401)

    def test_mfa_verify_with_correct_code_issues_a_real_token(self):
        secret, _ = self._setup_and_confirm_mfa()
        self._advance_to_next_totp_window()
        login = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        mfa_token = login.json()["mfa_token"]

        response = self.client.post("/api/auth/mfa/verify", json={"mfa_token": mfa_token, "code": self._code_at(secret)})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["access_token"])

    def test_mfa_verify_with_wrong_code_is_rejected(self):
        self._setup_and_confirm_mfa()
        login = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        mfa_token = login.json()["mfa_token"]

        response = self.client.post("/api/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "000000"})
        self.assertEqual(response.status_code, 401)

    def test_mfa_verify_with_unknown_mfa_token_is_rejected(self):
        response = self.client.post("/api/auth/mfa/verify", json={"mfa_token": "made-up-token", "code": "123456"})
        self.assertEqual(response.status_code, 401)

    def test_replayed_totp_code_is_rejected(self):
        """The named adversarial case: even a code that WAS valid a moment
        ago must not work twice, closing the window where an attacker who
        observes one valid code (shoulder-surfing, a compromised log) could
        reuse it."""
        secret, _ = self._setup_and_confirm_mfa()
        self._advance_to_next_totp_window()
        code = self._code_at(secret)

        login = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        first = self.client.post("/api/auth/mfa/verify", json={"mfa_token": login.json()["mfa_token"], "code": code})
        self.assertEqual(first.status_code, 200)

        # Second login attempt, replaying the SAME code at the SAME fake
        # "now" (a fresh mfa_token isolates the TOTP-replay check
        # specifically from the separately-tested mfa_token-replay check).
        login2 = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        replay = self.client.post("/api/auth/mfa/verify", json={"mfa_token": login2.json()["mfa_token"], "code": code})
        self.assertEqual(replay.status_code, 401)

    def test_replayed_mfa_token_is_rejected(self):
        """The mfa_token itself (not just the TOTP code) is single-use -
        closes a would-be second login off the same password-verification
        step."""
        secret, _ = self._setup_and_confirm_mfa()
        self._advance_to_next_totp_window()
        code = self._code_at(secret)
        login = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        mfa_token = login.json()["mfa_token"]

        first = self.client.post("/api/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code})
        self.assertEqual(first.status_code, 200)

        replay = self.client.post("/api/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code})
        self.assertEqual(replay.status_code, 401)

    # -- backup codes ------------------------------------------------

    def test_login_via_backup_code_works(self):
        _, backup_codes = self._setup_and_confirm_mfa()
        login = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        mfa_token = login.json()["mfa_token"]

        response = self.client.post("/api/auth/mfa/verify", json={"mfa_token": mfa_token, "code": backup_codes[0]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["access_token"])

    def test_backup_code_is_single_use(self):
        _, backup_codes = self._setup_and_confirm_mfa()

        login1 = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        first = self.client.post(
            "/api/auth/mfa/verify", json={"mfa_token": login1.json()["mfa_token"], "code": backup_codes[0]},
        )
        self.assertEqual(first.status_code, 200)

        login2 = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        second = self.client.post(
            "/api/auth/mfa/verify", json={"mfa_token": login2.json()["mfa_token"], "code": backup_codes[0]},
        )
        self.assertEqual(second.status_code, 401)

    def test_other_backup_codes_remain_valid_after_one_is_used(self):
        _, backup_codes = self._setup_and_confirm_mfa()

        login1 = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        self.client.post("/api/auth/mfa/verify", json={"mfa_token": login1.json()["mfa_token"], "code": backup_codes[0]})

        login2 = self.client.post("/api/auth/token", json={"username": "alice", "password": "s3cret-password!"})
        response = self.client.post(
            "/api/auth/mfa/verify", json={"mfa_token": login2.json()["mfa_token"], "code": backup_codes[1]},
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
