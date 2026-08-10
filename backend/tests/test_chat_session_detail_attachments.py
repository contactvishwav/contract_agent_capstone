"""
Stage 3 (frontend attach/preview/render UI) needs GET /api/chat/sessions/
{session_id} to expose which attachments belong to which message, so a
restored session can resolve attachment_id -> a real authenticated image
fetch. Drives the real get_session_detail route function directly, same
pattern as test_chat_session_tenant_isolation.py.
"""

import asyncio
import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api import chat_sessions as chat_sessions_api
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
    from backend.governance.auth import TokenIdentity

from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph


def _make_repo():
    graph = FakeChatAttachmentGraph()
    repo = Neo4jChatSessionRepository()
    repo.graph = graph
    return repo, graph


class SessionDetailAttachmentsTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.graph = _make_repo()
        self.session = self.repo.create_session("tenant_a", None, "Image chat")
        self.sid = self.session["session_id"]

    def _get_detail(self, tenant_id="tenant_a"):
        with patch.object(chat_sessions_api, "repository", self.repo):
            return asyncio.run(chat_sessions_api.get_session_detail(
                session_id=self.sid, identity=TokenIdentity(tenant_id=tenant_id, role="ADMIN"),
            ))

    def test_user_message_with_attachments_exposes_them(self):
        message = self.repo.append_message(self.sid, "tenant_a", role="user_message", content="What is this?")
        self.repo.create_attachment(self.sid, "tenant_a", "ATTACH_1", "image/png", 100, "a" * 64)
        self.repo.link_attachment_to_message("ATTACH_1", message["message_id"], "tenant_a")

        detail = self._get_detail()

        user_row = next(m for m in detail.messages if m.role == "user_message")
        self.assertEqual(user_row.attachments, [{"attachment_id": "ATTACH_1", "mime_type": "image/png"}])

    def test_user_message_without_attachments_has_empty_list(self):
        self.repo.append_message(self.sid, "tenant_a", role="user_message", content="hi")
        detail = self._get_detail()
        user_row = next(m for m in detail.messages if m.role == "user_message")
        self.assertEqual(user_row.attachments, [])

    def test_ai_message_never_carries_attachments(self):
        """Attachments are only ever linked to user_message rows
        (runner() links them to the persisted user turn) - ai_message rows
        must never report any, regardless of what exists on the session."""
        self.repo.append_message(self.sid, "tenant_a", role="user_message", content="hi")
        ai_message = self.repo.append_message(self.sid, "tenant_a", role="ai_message", content="hello")
        self.repo.create_attachment(self.sid, "tenant_a", "ATTACH_1", "image/png", 100, "a" * 64)
        # Deliberately (mis)link to the ai_message to prove the route still
        # never surfaces it there - a defense-in-depth check, not just an
        # absence-of-data test.
        self.repo.link_attachment_to_message("ATTACH_1", ai_message["message_id"], "tenant_a")

        detail = self._get_detail()

        ai_row = next(m for m in detail.messages if m.role == "ai_message")
        self.assertEqual(ai_row.attachments, [])

    def test_multiple_attachments_preserve_order(self):
        message = self.repo.append_message(self.sid, "tenant_a", role="user_message", content="Compare these")
        self.repo.create_attachment(self.sid, "tenant_a", "ATTACH_1", "image/png", 100, "a" * 64)
        self.repo.create_attachment(self.sid, "tenant_a", "ATTACH_2", "image/jpeg", 200, "b" * 64)
        self.repo.link_attachment_to_message("ATTACH_1", message["message_id"], "tenant_a")
        self.repo.link_attachment_to_message("ATTACH_2", message["message_id"], "tenant_a")

        detail = self._get_detail()

        user_row = next(m for m in detail.messages if m.role == "user_message")
        self.assertEqual(
            user_row.attachments,
            [
                {"attachment_id": "ATTACH_1", "mime_type": "image/png"},
                {"attachment_id": "ATTACH_2", "mime_type": "image/jpeg"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
