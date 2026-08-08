"""
Cross-tenant isolation regression tests for persistent Contract Chat
sessions - the required test from the feature's design plan ("a session/
message must never be readable by a different tenant").

Same in-memory FakeChatSessionGraph as test_chat_session_repository.py,
plus route-level coverage driving backend/api/chat_sessions.py's real
async route functions directly (matching test_policy_repository_tenant_
isolation.py's TestPolicyApiRoutesRejectCrossTenant pattern) - including
the create_session route's cross-tenant contract_id validation, which sits
outside the repository entirely.
"""

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
    from backend.api import chat_sessions as chat_sessions_api
    from backend.governance.auth import TokenIdentity

from backend.tests.test_chat_session_repository import FakeChatSessionGraph


def make_repo():
    graph = FakeChatSessionGraph()
    repo = Neo4jChatSessionRepository()
    repo.graph = graph
    return repo, graph


class RepositoryLevelCrossTenantTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.graph = make_repo()
        self.session = self.repo.create_session("tenant_a", "CONTRACT_1", "Tenant A's chat")
        self.sid = self.session["session_id"]
        self.repo.append_message(self.sid, "tenant_a", role="user_message", content="What's the fee?")

    def test_get_session_rejects_cross_tenant(self):
        self.assertIsNotNone(self.repo.get_session(self.sid, "tenant_a"))
        self.assertIsNone(self.repo.get_session(self.sid, "tenant_b"))

    def test_list_messages_rejects_cross_tenant(self):
        self.assertEqual(len(self.repo.list_messages(self.sid, "tenant_a")), 1)
        self.assertEqual(self.repo.list_messages(self.sid, "tenant_b"), [])

    def test_append_message_rejects_cross_tenant(self):
        result = self.repo.append_message(self.sid, "tenant_b", role="user_message", content="attacker message")
        self.assertIsNone(result)
        # Tenant A's own transcript must be completely unaffected by the
        # rejected cross-tenant attempt.
        messages = self.repo.list_messages(self.sid, "tenant_a")
        self.assertEqual(len(messages), 1)
        self.assertNotIn("attacker message", [m["content"] for m in messages])

    def test_list_sessions_never_returns_another_tenants_sessions(self):
        self.repo.create_session("tenant_b", "CONTRACT_1", "Tenant B's chat")
        tenant_a_sessions = self.repo.list_sessions("tenant_a")
        self.assertTrue(all(s["session_id"] != "TENANT_B_SESSION" for s in tenant_a_sessions))
        self.assertEqual(len(tenant_a_sessions), 1)


class RouteLevelCrossTenantTests(unittest.TestCase):
    """Drives the real chat_sessions.py route functions - list_sessions,
    create_session, get_session_detail - each of which constructs its own
    module-level `repository`/`contract_repository`, so those are patched
    onto our fixtures rather than mocked away entirely."""

    def setUp(self):
        self.repo, self.graph = make_repo()
        self.session = self.repo.create_session("tenant_a", "CONTRACT_1", "Tenant A's chat")
        self.sid = self.session["session_id"]
        self.repo.append_message(self.sid, "tenant_a", role="user_message", content="What's the fee?")

    def test_get_session_detail_route_404s_for_wrong_tenant(self):
        with patch.object(chat_sessions_api, "repository", self.repo):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(chat_sessions_api.get_session_detail(
                    session_id=self.sid, identity=TokenIdentity(tenant_id="tenant_b", role="ADMIN"),
                ))
            self.assertEqual(cm.exception.status_code, 404)

            # Confirm the real owner still gets the real data through the
            # same route.
            result = asyncio.run(chat_sessions_api.get_session_detail(
                session_id=self.sid, identity=TokenIdentity(tenant_id="tenant_a", role="ADMIN"),
            ))
        self.assertEqual(result.session_id, self.sid)
        self.assertEqual(len(result.messages), 1)

    def test_list_sessions_route_never_leaks_another_tenants_sessions(self):
        self.repo.create_session("tenant_b", "CONTRACT_1", "Tenant B's private chat")
        with patch.object(chat_sessions_api, "repository", self.repo):
            result = asyncio.run(chat_sessions_api.list_sessions(
                contract_id=None, identity=TokenIdentity(tenant_id="tenant_a", role="ADMIN"),
            ))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].session_id, self.sid)

    def test_create_session_route_404s_when_contract_id_belongs_to_another_tenant(self):
        """The real gap this check closes: without it, a client could scope
        a new session to a contract_id it doesn't own, which would then
        flow into EnhancedContractSearchTool via config["configurable"][
        "contract_id"] on every message sent in that session."""
        contract_repo_stub = _FakeContractOwnershipRepo(owned_by={"CONTRACT_1": "tenant_a"})

        with patch.object(chat_sessions_api, "repository", self.repo), \
             patch.object(chat_sessions_api, "contract_repository", contract_repo_stub):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(chat_sessions_api.create_session(
                    payload=chat_sessions_api.CreateSessionRequest(contract_id="CONTRACT_1", title="Sneaky"),
                    identity=TokenIdentity(tenant_id="tenant_b", role="ADMIN"),
                ))
            self.assertEqual(cm.exception.status_code, 404)

            # The real owner can still create a session scoped to their own contract.
            result = asyncio.run(chat_sessions_api.create_session(
                payload=chat_sessions_api.CreateSessionRequest(contract_id="CONTRACT_1", title="Legit"),
                identity=TokenIdentity(tenant_id="tenant_a", role="ADMIN"),
            ))
        self.assertEqual(result.contract_id, "CONTRACT_1")


class _FakeContractOwnershipRepo:
    """Minimal stand-in for Neo4jContractRepository, just enough to answer
    chat_sessions.py's create_session ownership-check query."""

    def __init__(self, owned_by):
        self.owned_by = owned_by
        self.graph = self

    def query(self, cypher, params=None):
        params = params or {}
        contract_id, tenant_id = params.get("contract_id"), params.get("tenant_id")
        if self.owned_by.get(contract_id) == tenant_id:
            return [{"file_id": contract_id}]
        return []


if __name__ == "__main__":
    unittest.main()
