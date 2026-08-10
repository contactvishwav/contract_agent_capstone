"""
Neo4jChatSessionRepository's attachment methods (ADR-008) - create, get,
link-to-message, list-for-message - against an in-memory fake graph
extending test_chat_session_repository.py's FakeChatSessionGraph with just
enough understanding of the new ChatAttachment Cypher shapes.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

from backend.tests.test_chat_session_repository import FakeChatSessionGraph, _FakeNeo4jDateTime


class FakeChatAttachmentGraph(FakeChatSessionGraph):
    """Extends the session/message fake with ChatAttachment support -
    mirrors real Neo4j MATCH semantics: an attachment is only found when
    every property in the MATCH pattern agrees with what's stored."""

    def __init__(self):
        super().__init__()
        self.attachments = {}  # attachment_id -> dict
        self.links = {}  # attachment_id -> message_id

    def query(self, cypher, params=None):
        params = params or {}

        if "CREATE (a:ChatAttachment" in cypher:
            session = self._lookup_session(params)
            if not session:
                return []
            self._clock += 1
            attachment = {
                "attachment_id": params["attachment_id"], "tenant_id": params["tenant_id"],
                "session_id": params["session_id"], "mime_type": params["mime_type"],
                "size_bytes": params["size_bytes"], "storage_key": params["storage_key"],
                "created_at": _FakeNeo4jDateTime(self._clock),
            }
            self.attachments[params["attachment_id"]] = attachment
            return [dict(attachment)]

        if "CREATE (m)-[:HAS_ATTACHMENT]->(a)" in cypher:
            attachment = self.attachments.get(params["attachment_id"])
            message = None
            for msgs in self.messages.values():
                message = next((m for m in msgs if m["message_id"] == params["message_id"]), None)
                if message:
                    break
            if not attachment or attachment["tenant_id"] != params["tenant_id"]:
                return []
            if not message:
                return []
            self.links[params["attachment_id"]] = params["message_id"]
            return [{"attachment_id": attachment["attachment_id"]}]

        if "(m:ChatMessage {message_id: $message_id, tenant_id: $tenant_id})-[:HAS_ATTACHMENT]->" in cypher:
            linked = [
                dict(a) for a in self.attachments.values()
                if self.links.get(a["attachment_id"]) == params["message_id"] and a["tenant_id"] == params["tenant_id"]
            ]
            linked.sort(key=lambda a: a["created_at"])
            return linked

        if "MATCH (a:ChatAttachment {attachment_id:" in cypher:
            attachment = self.attachments.get(params["attachment_id"])
            if not attachment:
                return []
            if attachment["tenant_id"] != params["tenant_id"] or attachment["session_id"] != params["session_id"]:
                return []
            return [dict(attachment)]

        return super().query(cypher, params)


def make_repo():
    graph = FakeChatAttachmentGraph()
    repo = Neo4jChatSessionRepository()
    repo.graph = graph
    return repo, graph


class CreateAttachmentTests(unittest.TestCase):
    def test_creates_attachment_scoped_to_an_existing_session(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        row = repo.create_attachment(
            session["session_id"], "tenant_a", "ATTACH_1", "image/png", 1234, "a" * 64,
        )
        self.assertEqual(row["attachment_id"], "ATTACH_1")
        self.assertEqual(row["mime_type"], "image/png")
        self.assertEqual(row["size_bytes"], 1234)

    def test_returns_none_for_unknown_session(self):
        repo, graph = make_repo()
        row = repo.create_attachment("SESSION_DOES_NOT_EXIST", "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)
        self.assertIsNone(row)

    def test_returns_none_for_cross_tenant_session(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        row = repo.create_attachment(session["session_id"], "tenant_b", "ATTACH_1", "image/png", 1, "a" * 64)
        self.assertIsNone(row)


class GetAttachmentTests(unittest.TestCase):
    def test_returns_attachment_for_owning_tenant_and_session(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        repo.create_attachment(sid, "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)

        row = repo.get_attachment("ATTACH_1", "tenant_a", sid)
        self.assertIsNotNone(row)
        self.assertEqual(row["storage_key"], "a" * 64)

    def test_returns_none_for_wrong_tenant(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        repo.create_attachment(sid, "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)

        self.assertIsNone(repo.get_attachment("ATTACH_1", "tenant_b", sid))

    def test_returns_none_for_wrong_session_even_same_tenant(self):
        """A tenant's own attachment from a DIFFERENT one of their own
        sessions must still be rejected - the URL path's session_id has to
        match exactly."""
        repo, graph = make_repo()
        session_a = repo.create_session("tenant_a", None, "Chat A")
        session_b = repo.create_session("tenant_a", None, "Chat B")
        repo.create_attachment(session_a["session_id"], "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)

        self.assertIsNone(repo.get_attachment("ATTACH_1", "tenant_a", session_b["session_id"]))

    def test_returns_none_for_unknown_attachment_id(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        self.assertIsNone(repo.get_attachment("ATTACH_DOES_NOT_EXIST", "tenant_a", session["session_id"]))


class LinkAttachmentToMessageTests(unittest.TestCase):
    def test_links_an_owned_attachment_to_an_owned_message(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        repo.create_attachment(sid, "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)
        message = repo.append_message(sid, "tenant_a", role="user_message", content="see attached")

        linked = repo.link_attachment_to_message("ATTACH_1", message["message_id"], "tenant_a")
        self.assertTrue(linked)

        attachments = repo.list_attachments_for_message(message["message_id"], "tenant_a")
        self.assertEqual([a["attachment_id"] for a in attachments], ["ATTACH_1"])

    def test_cannot_link_a_different_tenants_attachment(self):
        repo, graph = make_repo()
        session_a = repo.create_session("tenant_a", None, "Chat")
        session_b = repo.create_session("tenant_b", None, "Chat")
        repo.create_attachment(session_a["session_id"], "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)
        message_b = repo.append_message(session_b["session_id"], "tenant_b", role="user_message", content="hi")

        linked = repo.link_attachment_to_message("ATTACH_1", message_b["message_id"], "tenant_b")
        self.assertFalse(linked)
        self.assertEqual(repo.list_attachments_for_message(message_b["message_id"], "tenant_b"), [])

    def test_returns_false_for_unknown_attachment_or_message(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        message = repo.append_message(session["session_id"], "tenant_a", role="user_message", content="hi")

        self.assertFalse(repo.link_attachment_to_message("ATTACH_DOES_NOT_EXIST", message["message_id"], "tenant_a"))
        repo.create_attachment(session["session_id"], "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)
        self.assertFalse(repo.link_attachment_to_message("ATTACH_1", "MSG_DOES_NOT_EXIST", "tenant_a"))


class ListAttachmentsForMessageTests(unittest.TestCase):
    def test_empty_when_message_has_no_attachments(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        message = repo.append_message(session["session_id"], "tenant_a", role="user_message", content="no image")
        self.assertEqual(repo.list_attachments_for_message(message["message_id"], "tenant_a"), [])

    def test_returns_multiple_attachments_in_creation_order(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        message = repo.append_message(sid, "tenant_a", role="user_message", content="two images")
        repo.create_attachment(sid, "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)
        repo.create_attachment(sid, "tenant_a", "ATTACH_2", "image/jpeg", 2, "b" * 64)
        repo.link_attachment_to_message("ATTACH_1", message["message_id"], "tenant_a")
        repo.link_attachment_to_message("ATTACH_2", message["message_id"], "tenant_a")

        attachments = repo.list_attachments_for_message(message["message_id"], "tenant_a")
        self.assertEqual([a["attachment_id"] for a in attachments], ["ATTACH_1", "ATTACH_2"])

    def test_empty_for_cross_tenant_message_id(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        message = repo.append_message(session["session_id"], "tenant_a", role="user_message", content="hi")
        self.assertEqual(repo.list_attachments_for_message(message["message_id"], "tenant_b"), [])


if __name__ == "__main__":
    unittest.main()
