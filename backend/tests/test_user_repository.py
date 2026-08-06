"""
Real credential storage tests (closes the last piece of the README's
"Real Authenticated Tenant Identity" item - api/auth_api.py has the route-
level rationale).

Covers UserRepository in isolation: real bcrypt hashing (never plaintext,
never reversible), correct-credentials verification, wrong-password
rejection, unknown-username rejection, duplicate-username rejection, and
basic input validation. Extended (credential-provisioning engagement) with
the SSO/TOTP/tenant-bootstrap methods added alongside - FakeUserGraph
below grew the matching new Cypher shapes.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.user_repository import (
        TenantAlreadyProvisionedError,
        UserRepository,
        UsernameAlreadyExistsError,
    )


class FakeUserGraph:
    """
    In-memory stand-in for Neo4jGraph, dispatching on distinguishing
    substrings of each Cypher shape UserRepository issues - same idiom as
    the original (pre-credential-provisioning) version of this class, just
    with more branches. Ordered most-specific-first: several later
    branches' distinguishing substrings ("password_hash", "backup_codes_
    hashed = $hashed", etc.) would otherwise also match the broad, generic
    "MATCH (u:User {username: $username})" prefix every username-keyed
    query starts with - that catch-all existence check is deliberately
    last, exactly as it was in the original file.
    """

    def __init__(self):
        self.users = {}

    def query(self, cypher: str, params: dict = None):
        params = params or {}
        cypher_stripped = cypher.strip()

        if cypher_stripped.startswith("CREATE (u:User"):
            self.users[params["username"]] = dict(params)
            return []

        if "password_hash" in cypher:
            user = self.users.get(params.get("username"))
            if not user:
                return []
            return [{
                "username": user["username"],
                "password_hash": user.get("password_hash"),
                "tenant_id": user["tenant_id"],
                "role": user["role"],
            }]

        if cypher_stripped.startswith("MATCH (u:User {sso_provider:"):
            for user in self.users.values():
                if user.get("sso_provider") == params.get("sso_provider") and user.get("sso_subject") == params.get("sso_subject"):
                    return [{"username": user["username"], "tenant_id": user["tenant_id"], "role": user["role"]}]
            return []

        if "totp_secret_encrypted as totp_secret_encrypted" in cypher:
            user = self.users.get(params.get("username"))
            if not user:
                return []
            return [{
                "totp_secret_encrypted": user.get("totp_secret_encrypted"),
                "mfa_enabled": user.get("mfa_enabled", False),
                "mfa_last_used_step": user.get("mfa_last_used_step"),
            }]

        if "totp_secret_encrypted = $encrypted" in cypher:
            user = self.users.get(params.get("username"))
            if user:
                user["totp_secret_encrypted"] = params["encrypted"]
                user["mfa_enabled"] = False
            return []

        if "backup_codes_hashed = $hashed" in cypher:
            user = self.users.get(params.get("username"))
            if user:
                user["mfa_enabled"] = True
                user["backup_codes_hashed"] = params["hashed"]
            return []

        if "mfa_last_used_step = $step" in cypher:
            user = self.users.get(params.get("username"))
            if user:
                user["mfa_last_used_step"] = params["step"]
            return []

        if "backup_codes_hashed as backup_codes_hashed" in cypher:
            user = self.users.get(params.get("username"))
            if not user:
                return []
            return [{"backup_codes_hashed": user.get("backup_codes_hashed", [])}]

        if "backup_codes_hashed = $remaining" in cypher:
            user = self.users.get(params.get("username"))
            if user:
                user["backup_codes_hashed"] = params["remaining"]
            return []

        if "tenant_id: $tenant_id" in cypher and "LIMIT 1" in cypher:
            for user in self.users.values():
                if user.get("tenant_id") == params.get("tenant_id"):
                    return [{"username": user["username"]}]
            return []

        if cypher_stripped.startswith(
            "MATCH (u:User {username: $username}) RETURN u.username as username, u.tenant_id as tenant_id, u.role as role"
        ):
            user = self.users.get(params.get("username"))
            if not user:
                return []
            return [{"username": user["username"], "tenant_id": user["tenant_id"], "role": user["role"]}]

        if cypher_stripped.startswith("MATCH (u:User {username: $username})"):
            user = self.users.get(params.get("username"))
            return [{"username": user["username"]}] if user else []

        return []


class UserRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = UserRepository(graph_=FakeUserGraph())

    def test_create_user_stores_a_real_bcrypt_hash_not_plaintext(self):
        account = self.repo.create_user(
            username="alice", password="correct horse battery staple",
            tenant_id="tenant_a", role="ADMIN",
        )
        self.assertEqual(account.username, "alice")
        self.assertEqual(account.tenant_id, "tenant_a")
        self.assertEqual(account.role, "ADMIN")

        stored = self.repo.graph.users["alice"]
        self.assertNotEqual(stored["password_hash"], "correct horse battery staple")
        # bcrypt's own format marker - proves a real bcrypt hash was used,
        # not some ad-hoc reversible encoding.
        self.assertTrue(stored["password_hash"].startswith("$2b$"))

    def test_verify_credentials_succeeds_with_correct_password(self):
        self.repo.create_user(username="alice", password="s3cret-password!", tenant_id="tenant_a", role="ADMIN")

        account = self.repo.verify_credentials("alice", "s3cret-password!")

        self.assertIsNotNone(account)
        self.assertEqual(account.tenant_id, "tenant_a")
        self.assertEqual(account.role, "ADMIN")

    def test_verify_credentials_rejects_wrong_password(self):
        self.repo.create_user(username="alice", password="s3cret-password!", tenant_id="tenant_a", role="ADMIN")

        self.assertIsNone(self.repo.verify_credentials("alice", "wrong-password"))

    def test_verify_credentials_rejects_unknown_username(self):
        self.assertIsNone(self.repo.verify_credentials("nobody-registered", "whatever"))

    def test_create_user_rejects_duplicate_username(self):
        self.repo.create_user(username="alice", password="s3cret-password!", tenant_id="tenant_a", role="ADMIN")

        with self.assertRaises(UsernameAlreadyExistsError):
            self.repo.create_user(username="alice", password="a-different-password", tenant_id="tenant_b", role="VIEWER")

    def test_create_user_rejects_short_password(self):
        with self.assertRaises(ValueError):
            self.repo.create_user(username="bob", password="short", tenant_id="tenant_a", role="ADMIN")

    def test_create_user_rejects_invalid_username(self):
        with self.assertRaises(ValueError):
            self.repo.create_user(username="a b!", password="s3cret-password!", tenant_id="tenant_a", role="ADMIN")

    def test_create_user_rejects_empty_tenant_id(self):
        with self.assertRaises(ValueError):
            self.repo.create_user(username="carol", password="s3cret-password!", tenant_id="   ", role="ADMIN")


class TenantBootstrapScopeTests(unittest.TestCase):
    """enforce_tenant_bootstrap=True is what api/auth_api.py's POST
    /register actually uses - the real fix for the vulnerability this
    engagement's credential-provisioning work exists to close (previously
    any caller could self-register into any existing tenant_id)."""

    def setUp(self):
        self.repo = UserRepository(graph_=FakeUserGraph())

    def test_first_user_of_a_new_tenant_is_allowed(self):
        account = self.repo.create_user(
            username="founder", password="s3cret-password!", tenant_id="brand_new_tenant",
            role="ADMIN", enforce_tenant_bootstrap=True,
        )
        self.assertEqual(account.tenant_id, "brand_new_tenant")

    def test_second_user_of_an_already_provisioned_tenant_is_rejected(self):
        self.repo.create_user(
            username="founder", password="s3cret-password!", tenant_id="existing_tenant",
            role="ADMIN", enforce_tenant_bootstrap=True,
        )
        with self.assertRaises(TenantAlreadyProvisionedError):
            self.repo.create_user(
                username="interloper", password="another-password!", tenant_id="existing_tenant",
                role="ADMIN", enforce_tenant_bootstrap=True,
            )

    def test_duplicate_username_error_takes_priority_over_bootstrap_error(self):
        """Checked in this order deliberately (module docstring/create_user
        docstring): a duplicate-username attempt against an already-
        provisioned tenant should report the more specific, actionable
        error, not be masked by the tenant-bootstrap check."""
        self.repo.create_user(
            username="founder", password="s3cret-password!", tenant_id="existing_tenant",
            role="ADMIN", enforce_tenant_bootstrap=True,
        )
        with self.assertRaises(UsernameAlreadyExistsError):
            self.repo.create_user(
                username="founder", password="a-different-password!", tenant_id="existing_tenant",
                role="ADMIN", enforce_tenant_bootstrap=True,
            )

    def test_enforce_tenant_bootstrap_false_allows_joining_an_existing_tenant(self):
        """Invite-driven creation (api/auth_api.py's accept-invite route)
        always passes False - the invite itself is the authorization."""
        self.repo.create_user(
            username="founder", password="s3cret-password!", tenant_id="existing_tenant",
            role="ADMIN", enforce_tenant_bootstrap=True,
        )
        account = self.repo.create_user(
            username="invited_member", password="viewer-password!", tenant_id="existing_tenant",
            role="VIEWER", enforce_tenant_bootstrap=False,
        )
        self.assertEqual(account.tenant_id, "existing_tenant")

    def test_tenant_has_any_users(self):
        self.assertFalse(self.repo.tenant_has_any_users("empty_tenant"))
        self.repo.create_user(username="someone", password="s3cret-password!", tenant_id="populated_tenant", role="VIEWER")
        self.assertTrue(self.repo.tenant_has_any_users("populated_tenant"))


class SsoAccountTests(unittest.TestCase):
    def setUp(self):
        self.repo = UserRepository(graph_=FakeUserGraph())

    def test_create_sso_user_and_look_up_by_sso_identity(self):
        account = self.repo.create_sso_user(
            username="alice.g", tenant_id="tenant_a", role="VIEWER",
            email="alice@example.com", sso_provider="google", sso_subject="1234567890",
        )
        self.assertEqual(account.username, "alice.g")

        found = self.repo.get_user_by_sso("google", "1234567890")
        self.assertIsNotNone(found)
        self.assertEqual(found.username, "alice.g")
        self.assertEqual(found.tenant_id, "tenant_a")
        self.assertEqual(found.role, "VIEWER")

    def test_get_user_by_sso_returns_none_for_unknown_identity(self):
        self.assertIsNone(self.repo.get_user_by_sso("google", "no-such-subject"))

    def test_sso_only_account_has_no_usable_password(self):
        """An SSO-created account never sets password_hash at all -
        verify_credentials's existing `if not stored_hash: return None`
        already makes it correctly unable to log in via password, with no
        extra branching needed for this case."""
        self.repo.create_sso_user(
            username="alice.g", tenant_id="tenant_a", role="VIEWER",
            email="alice@example.com", sso_provider="google", sso_subject="1234567890",
        )
        self.assertIsNone(self.repo.verify_credentials("alice.g", "any-password-at-all"))

    def test_get_user_by_username_finds_an_sso_account(self):
        self.repo.create_sso_user(
            username="alice.g", tenant_id="tenant_a", role="VIEWER",
            email="alice@example.com", sso_provider="google", sso_subject="1234567890",
        )
        found = self.repo.get_user_by_username("alice.g")
        self.assertIsNotNone(found)
        self.assertEqual(found.tenant_id, "tenant_a")

    def test_get_user_by_username_returns_none_for_unknown_user(self):
        self.assertIsNone(self.repo.get_user_by_username("nobody"))


class TotpMfaRepositoryTests(unittest.TestCase):
    """FieldEncryptor (infrastructure/encryption.py) round-trips through
    its real dev-fallback key here (no ENCRYPTION_KEY set in the test
    environment) - real AES-256-GCM encrypt/decrypt, not a stub."""

    def setUp(self):
        self.repo = UserRepository(graph_=FakeUserGraph())
        self.repo.create_user(username="alice", password="s3cret-password!", tenant_id="tenant_a", role="ADMIN")

    def test_mfa_disabled_by_default(self):
        state = self.repo.get_totp_state("alice")
        self.assertIsNotNone(state)
        self.assertFalse(state.mfa_enabled)
        self.assertIsNone(state.totp_secret_encrypted)

    def test_set_pending_totp_secret_is_recoverable_and_not_yet_enabled(self):
        self.repo.set_pending_totp_secret("alice", "JBSWY3DPEHPK3PXP")

        self.assertEqual(self.repo.get_decrypted_totp_secret("alice"), "JBSWY3DPEHPK3PXP")
        state = self.repo.get_totp_state("alice")
        self.assertFalse(state.mfa_enabled, "must stay disabled until enable_mfa is called (post-confirmation)")

    def test_stored_secret_is_actually_encrypted_not_plaintext(self):
        self.repo.set_pending_totp_secret("alice", "JBSWY3DPEHPK3PXP")
        stored_raw = self.repo.graph.users["alice"]["totp_secret_encrypted"]
        self.assertNotEqual(stored_raw, "JBSWY3DPEHPK3PXP")

    def test_enable_mfa_flips_flag_and_stores_hashed_backup_codes(self):
        self.repo.set_pending_totp_secret("alice", "JBSWY3DPEHPK3PXP")
        self.repo.enable_mfa("alice", ["aaaa1111", "bbbb2222"])

        state = self.repo.get_totp_state("alice")
        self.assertTrue(state.mfa_enabled)
        stored_codes = self.repo.graph.users["alice"]["backup_codes_hashed"]
        self.assertNotIn("aaaa1111", stored_codes)
        self.assertTrue(all(c.startswith("$2b$") for c in stored_codes))

    def test_record_totp_step_used_persists(self):
        self.assertIsNone(self.repo.get_totp_state("alice").mfa_last_used_step)
        self.repo.record_totp_step_used("alice", 12345)
        self.assertEqual(self.repo.get_totp_state("alice").mfa_last_used_step, 12345)

    def test_consume_backup_code_succeeds_once_and_only_once(self):
        self.repo.enable_mfa("alice", ["aaaa1111", "bbbb2222"])

        self.assertTrue(self.repo.consume_backup_code("alice", "aaaa1111"))
        # The exact same code again - must fail (single-use).
        self.assertFalse(self.repo.consume_backup_code("alice", "aaaa1111"))
        # The other, still-unused code still works.
        self.assertTrue(self.repo.consume_backup_code("alice", "bbbb2222"))

    def test_consume_backup_code_rejects_unknown_code(self):
        self.repo.enable_mfa("alice", ["aaaa1111"])
        self.assertFalse(self.repo.consume_backup_code("alice", "not-a-real-code"))


if __name__ == "__main__":
    unittest.main()
