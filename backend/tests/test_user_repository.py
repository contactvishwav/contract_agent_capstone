"""
Real credential storage tests (closes the last piece of the README's
"Real Authenticated Tenant Identity" item - api/auth_api.py has the route-
level rationale).

Covers UserRepository in isolation: real bcrypt hashing (never plaintext,
never reversible), correct-credentials verification, wrong-password
rejection, unknown-username rejection, duplicate-username rejection, and
basic input validation.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.user_repository import (
        UserRepository,
        UsernameAlreadyExistsError,
    )


class FakeUserGraph:
    """Minimal in-memory stand-in for Neo4jGraph understanding just the two
    Cypher shapes UserRepository issues: a username-only existence check
    and a full credential-verification read, plus the CREATE write."""

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
                "password_hash": user["password_hash"],
                "tenant_id": user["tenant_id"],
                "role": user["role"],
            }]

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


if __name__ == "__main__":
    unittest.main()
