"""
Backend-side proof of design requirement 2's survival guarantee: a full
conversation turn written to a session must come back identically through
a completely independent read - no shared in-process state, no caching
between the write and the read - simulating what "navigate away and
back" / "refresh" / "log out and back in" all reduce to server-side: a
fresh request against durable storage.

This proves the persistence half of the requirement. The frontend's
get-session-on-select flow (loading a GET /api/chat/sessions/{id}
response into ChatProvider's message list) is what closes the loop for an
actual browser refresh - verified live per the feature's implementation
plan, not exercised by this backend suite.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

from backend.tests.test_chat_session_repository import FakeChatSessionGraph


class HistoryRestorationTests(unittest.TestCase):
    def test_full_turn_survives_a_completely_independent_read(self):
        # The "write" side: a real conversation turn about Clean_MSA.pdf,
        # persisted exactly the way runner() persists it in backend/main.py -
        # incoming prompt, a tool call, a tool result, then the final answer.
        graph = FakeChatSessionGraph()
        writer_repo = Neo4jChatSessionRepository()
        writer_repo.graph = graph

        session = writer_repo.create_session("tenant_a", "UPLOADED_MSA_1", "Payment terms")
        sid = session["session_id"]

        writer_repo.append_message(sid, "tenant_a", role="user_message", content="What are the payment terms?")
        writer_repo.append_message(
            sid, "tenant_a", role="tool_call",
            content='{"name": "EnhancedContractSearch", "args": {"summary_search": "payment terms"}}',
            tool_name="EnhancedContractSearch", tool_call_id="call_1",
        )
        writer_repo.append_message(
            sid, "tenant_a", role="tool_message",
            content="Payment due within 90 days, at Client's sole discretion.",
            tool_call_id="call_1",
        )
        writer_repo.append_message(
            sid, "tenant_a", role="ai_message",
            content="Payment is due within 90 days, at the Client's sole discretion.",
            model="gemini-2.5-flash",
        )

        # The "read" side: a brand-new repository instance against the same
        # underlying graph (standing in for a fresh HTTP request hitting a
        # fresh backend process against durable Neo4j storage) - nothing
        # about the writer_repo instance above is reused.
        reader_repo = Neo4jChatSessionRepository()
        reader_repo.graph = graph

        restored_session = reader_repo.get_session(sid, "tenant_a")
        restored_messages = reader_repo.list_messages(sid, "tenant_a")

        self.assertIsNotNone(restored_session)
        self.assertEqual(restored_session["title"], "Payment terms")
        self.assertEqual(restored_session["contract_id"], "UPLOADED_MSA_1")
        self.assertEqual(restored_session["message_count"], 4)

        self.assertEqual(
            [m["role"] for m in restored_messages],
            ["user_message", "tool_call", "tool_message", "ai_message"],
            "full turn, in order, must come back exactly as written",
        )
        self.assertEqual(restored_messages[0]["content"], "What are the payment terms?")
        self.assertEqual(
            restored_messages[3]["content"],
            "Payment is due within 90 days, at the Client's sole discretion.",
        )
        self.assertEqual(restored_messages[3]["model"], "gemini-2.5-flash")
        self.assertEqual(restored_messages[1]["tool_call_id"], "call_1")
        self.assertEqual(restored_messages[2]["tool_call_id"], "call_1")

    def test_a_second_later_turn_appends_after_the_first_without_disturbing_it(self):
        """Simulates reopening a session and continuing the conversation -
        the restored history from the first turn must still be exactly
        where it was once a second turn is added."""
        graph = FakeChatSessionGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph

        session = repo.create_session("tenant_a", "UPLOADED_MSA_1", "Payment terms")
        sid = session["session_id"]
        repo.append_message(sid, "tenant_a", role="user_message", content="What are the payment terms?")
        repo.append_message(sid, "tenant_a", role="ai_message", content="90 days.")

        # Simulate navigating away and back: read via a fresh instance.
        reopened_repo = Neo4jChatSessionRepository()
        reopened_repo.graph = graph
        first_turn = reopened_repo.list_messages(sid, "tenant_a")
        self.assertEqual(len(first_turn), 2)

        # Continue the conversation in the reopened session.
        reopened_repo.append_message(sid, "tenant_a", role="user_message", content="Is there a liability cap?")
        reopened_repo.append_message(sid, "tenant_a", role="ai_message", content="No, liability is unlimited.")

        final = reopened_repo.list_messages(sid, "tenant_a")
        self.assertEqual(len(final), 4)
        self.assertEqual(final[0]["content"], "What are the payment terms?")
        self.assertEqual(final[1]["content"], "90 days.")
        self.assertEqual(final[2]["content"], "Is there a liability cap?")
        self.assertEqual(final[3]["content"], "No, liability is unlimited.")


if __name__ == "__main__":
    unittest.main()
