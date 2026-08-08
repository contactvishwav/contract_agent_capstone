"""
The literal proof of design requirement 1: multiple sessions per contract
must be possible - a session LIST per contract, not one slot. A user
starting a fresh conversation about a contract they already have an open
thread on must get an independent, second session, not overwrite/reuse
the first.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

from backend.tests.test_chat_session_repository import FakeChatSessionGraph


def make_repo():
    graph = FakeChatSessionGraph()
    repo = Neo4jChatSessionRepository()
    repo.graph = graph
    return repo, graph


class MultipleSessionsPerContractTests(unittest.TestCase):
    def test_three_sessions_on_the_same_contract_are_all_independently_listed(self):
        repo, graph = make_repo()
        s1 = repo.create_session("tenant_a", "CONTRACT_1", "First read-through")
        s2 = repo.create_session("tenant_a", "CONTRACT_1", "Questions about termination")
        s3 = repo.create_session("tenant_a", "CONTRACT_1", "Follow-up next week")

        ids = {s1["session_id"], s2["session_id"], s3["session_id"]}
        self.assertEqual(len(ids), 3, "each new session must get a distinct id, never reusing an existing slot")

        rows = repo.list_sessions("tenant_a", contract_id="CONTRACT_1")
        self.assertEqual({r["session_id"] for r in rows}, ids)

    def test_appending_to_one_session_does_not_affect_a_sibling_sessions_messages_or_count(self):
        repo, graph = make_repo()
        s1 = repo.create_session("tenant_a", "CONTRACT_1", "Thread one")
        s2 = repo.create_session("tenant_a", "CONTRACT_1", "Thread two")

        repo.append_message(s1["session_id"], "tenant_a", role="user_message", content="Only in thread one")
        repo.append_message(s1["session_id"], "tenant_a", role="ai_message", content="Reply in thread one")

        self.assertEqual(len(repo.list_messages(s1["session_id"], "tenant_a")), 2)
        self.assertEqual(len(repo.list_messages(s2["session_id"], "tenant_a")), 0)

        thread_two_meta = repo.get_session(s2["session_id"], "tenant_a")
        self.assertEqual(thread_two_meta["message_count"], 0)

    def test_all_contracts_sessions_coexist_with_contract_scoped_sessions(self):
        """A user can have both a contract-specific thread and a separate
        All-Contracts thread open at once, independently."""
        repo, graph = make_repo()
        contract_thread = repo.create_session("tenant_a", "CONTRACT_1", "About this contract")
        all_contracts_thread = repo.create_session("tenant_a", None, "General question across contracts")

        self.assertNotEqual(contract_thread["session_id"], all_contracts_thread["session_id"])

        all_sessions = repo.list_sessions("tenant_a")
        self.assertEqual(len(all_sessions), 2)

        only_contract_1 = repo.list_sessions("tenant_a", contract_id="CONTRACT_1")
        self.assertEqual(len(only_contract_1), 1)
        self.assertEqual(only_contract_1[0]["session_id"], contract_thread["session_id"])


if __name__ == "__main__":
    unittest.main()
