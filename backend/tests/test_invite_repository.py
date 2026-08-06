"""
Org invite token lifecycle (credential-provisioning engagement, docs
design report). Covers InviteRepository in isolation: token hashing (never
stored in plaintext), preview vs. consume semantics, single-use
enforcement, and expiry.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.invite_repository import InviteRepository, _hash_token


class FakeInviteGraph:
    """
    In-memory stand-in for Neo4jGraph, understanding the CREATE, preview
    MATCH (no SET), and consume MATCH+WHERE+SET Cypher shapes
    InviteRepository issues. Applies the expiry/used_at gating in Python,
    mirroring exactly what the real Cypher's WHERE clause enforces -
    single-use and expiry are the properties under test here, not Neo4j
    itself.
    """

    def __init__(self):
        self.invites = {}  # token_hash -> dict

    def query(self, cypher: str, params: dict = None):
        params = params or {}
        cypher_stripped = cypher.strip()

        if cypher_stripped.startswith("CREATE (i:Invite"):
            self.invites[params["token_hash"]] = {
                "email": params["email"],
                "tenant_id": params["tenant_id"],
                "role": params["role"],
                "invited_by": params["invited_by"],
                "expires_at": datetime.fromisoformat(params["expires_at"]),
                "used_at": None,
            }
            return []

        if "SET i.used_at = datetime()" in cypher:
            # consume_invite: atomic MATCH+WHERE+SET
            record = self.invites.get(params.get("token_hash"))
            if not record:
                return []
            now = datetime.fromisoformat(params["now"])
            if record["used_at"] is not None or record["expires_at"] <= now:
                return []
            record["used_at"] = datetime.now(timezone.utc)
            return [self._row(record)]

        if cypher_stripped.startswith("MATCH (i:Invite {token_hash:"):
            # get_invite: preview only, no mutation
            record = self.invites.get(params.get("token_hash"))
            if not record:
                return []
            return [self._row(record)]

        return []

    @staticmethod
    def _row(record: dict) -> dict:
        return {
            "email": record["email"],
            "tenant_id": record["tenant_id"],
            "role": record["role"],
            "invited_by": record["invited_by"],
            "expires_at": record["expires_at"].isoformat(),
            "used_at": record["used_at"].isoformat() if record["used_at"] else None,
        }


class InviteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = InviteRepository(graph_=FakeInviteGraph())

    def test_create_invite_returns_a_usable_raw_token(self):
        token = self.repo.create_invite(email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice")
        self.assertTrue(token)
        self.assertEqual(len(self.repo.graph.invites), 1)

    def test_raw_token_is_never_stored_directly(self):
        token = self.repo.create_invite(email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice")
        stored_hashes = self.repo.graph.invites.keys()
        self.assertNotIn(token, stored_hashes)
        self.assertIn(_hash_token(token), stored_hashes)

    def test_get_invite_preview_does_not_consume_it(self):
        token = self.repo.create_invite(email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice")

        first = self.repo.get_invite(token)
        second = self.repo.get_invite(token)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second, "preview must not consume the invite")
        self.assertEqual(first.email, "bob@example.com")
        self.assertEqual(first.tenant_id, "tenant_a")
        self.assertEqual(first.role, "VIEWER")

    def test_get_invite_returns_none_for_unknown_token(self):
        self.assertIsNone(self.repo.get_invite("not-a-real-token"))

    def test_consume_invite_succeeds_once(self):
        token = self.repo.create_invite(email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice")

        record = self.repo.consume_invite(token)

        self.assertIsNotNone(record)
        self.assertEqual(record.email, "bob@example.com")
        self.assertIsNotNone(record.used_at)

    def test_consume_invite_cannot_be_replayed(self):
        """The core single-use guarantee: a second accept attempt with the
        same token must fail, not silently create a second account."""
        token = self.repo.create_invite(email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice")

        first = self.repo.consume_invite(token)
        second = self.repo.consume_invite(token)

        self.assertIsNotNone(first)
        self.assertIsNone(second, "a replayed invite token must not consume successfully twice")

    def test_get_invite_after_consumption_returns_none(self):
        """Preview must also treat a used invite as gone (same generic
        "not found" the docstring promises) - not distinguishable from an
        expired or unknown token by a caller probing it."""
        token = self.repo.create_invite(email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice")
        self.repo.consume_invite(token)

        self.assertIsNone(self.repo.get_invite(token))

    def test_expired_invite_cannot_be_consumed(self):
        token = self.repo.create_invite(
            email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice", ttl_days=-1,
        )

        self.assertIsNone(self.repo.get_invite(token))
        self.assertIsNone(self.repo.consume_invite(token))

    def test_tampered_token_does_not_match_any_stored_invite(self):
        """A token isn't stored in plaintext (sha256'd) - a caller cannot
        derive a working token by tweaking a known/leaked hash, or vice
        versa; only the exact original raw token hashes to a match."""
        token = self.repo.create_invite(email="bob@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="alice")
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

        self.assertIsNone(self.repo.get_invite(tampered))
        self.assertIsNone(self.repo.consume_invite(tampered))

    def test_two_invites_for_different_tenants_are_independent(self):
        """Sanity check for the property api/auth_api.py's create_invite
        route depends on: an invite is intrinsically scoped to one
        tenant_id, baked in at creation - there is no way to later use one
        tenant's invite to join a different tenant."""
        token_a = self.repo.create_invite(email="x@example.com", tenant_id="tenant_a", role="VIEWER", invited_by="admin_a")
        token_b = self.repo.create_invite(email="y@example.com", tenant_id="tenant_b", role="ADMIN", invited_by="admin_b")

        record_a = self.repo.consume_invite(token_a)
        record_b = self.repo.consume_invite(token_b)

        self.assertEqual(record_a.tenant_id, "tenant_a")
        self.assertEqual(record_b.tenant_id, "tenant_b")

    def test_create_invite_rejects_empty_email(self):
        with self.assertRaises(ValueError):
            self.repo.create_invite(email="   ", tenant_id="tenant_a", role="VIEWER", invited_by="alice")

    def test_create_invite_rejects_empty_tenant_id(self):
        with self.assertRaises(ValueError):
            self.repo.create_invite(email="bob@example.com", tenant_id="  ", role="VIEWER", invited_by="alice")


if __name__ == "__main__":
    unittest.main()
