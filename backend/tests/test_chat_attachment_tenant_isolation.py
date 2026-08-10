"""
Cross-tenant/cross-session isolation regression tests for Contract Chat
image attachments (ADR-008) - same rigor and pattern as
test_chat_session_tenant_isolation.py: repository-level assertions plus
route-level coverage driving backend/api/chat_sessions.py's real async
route functions directly.
"""

import asyncio
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from starlette.requests import Request

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chat_attachment_storage import ChatAttachmentStorage
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
    from backend.api import chat_sessions as chat_sessions_api
    from backend.governance.auth import TokenIdentity

from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844410000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100dcba79820000"
    "000049454e44ae426082"
)


class _FixedKeyProvider:
    def get_key(self) -> bytes:
        return b"\x24" * 32


def _make_repo():
    graph = FakeChatAttachmentGraph()
    repo = Neo4jChatSessionRepository()
    repo.graph = graph
    return repo, graph


def _make_upload_file(content: bytes = PNG_1X1, filename: str = "photo.png") -> UploadFile:
    return UploadFile(filename=filename, file=__import__("io").BytesIO(content), headers=Headers({"content-type": "image/png"}))


def _fake_request() -> Request:
    """upload_attachment carries @limiter.limit (same as main.py's run()),
    which requires a real starlette Request positional argument to key the
    rate-limit check on - this file calls the route function directly as a
    coroutine, bypassing the ASGI/TestClient layer, so it must build one by
    hand."""
    return Request({
        "type": "http", "method": "POST", "path": "/api/chat/sessions/x/attachments",
        "headers": [], "client": ("testclient", 123), "server": ("testserver", 80),
        "scheme": "http", "query_string": b"",
    })


class RepositoryLevelCrossTenantAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.graph = _make_repo()
        self.session = self.repo.create_session("tenant_a", None, "Tenant A's chat")
        self.sid = self.session["session_id"]
        self.repo.create_attachment(self.sid, "tenant_a", "ATTACH_1", "image/png", 100, "a" * 64)

    def test_get_attachment_rejects_wrong_tenant(self):
        self.assertIsNotNone(self.repo.get_attachment("ATTACH_1", "tenant_a", self.sid))
        self.assertIsNone(self.repo.get_attachment("ATTACH_1", "tenant_b", self.sid))

    def test_create_attachment_rejects_a_session_belonging_to_another_tenant(self):
        result = self.repo.create_attachment(self.sid, "tenant_b", "ATTACK_1", "image/png", 1, "b" * 64)
        self.assertIsNone(result)
        # Tenant A's session must be completely unaffected.
        self.assertIsNotNone(self.repo.get_attachment("ATTACH_1", "tenant_a", self.sid))

    def test_link_attachment_rejects_cross_tenant_message(self):
        other_session = self.repo.create_session("tenant_b", None, "Tenant B's chat")
        other_message = self.repo.append_message(other_session["session_id"], "tenant_b", role="user_message", content="hi")

        linked = self.repo.link_attachment_to_message("ATTACH_1", other_message["message_id"], "tenant_b")
        self.assertFalse(linked, "tenant B must never be able to link tenant A's attachment to its own message")


class RouteLevelCrossTenantAttachmentTests(unittest.TestCase):
    """Drives the real upload_attachment/get_attachment route functions
    directly, same style as test_chat_session_tenant_isolation.py's
    RouteLevelCrossTenantTests."""

    def setUp(self):
        self.repo, self.graph = _make_repo()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.storage = ChatAttachmentStorage(root=self.tmpdir.name, key_provider=_FixedKeyProvider())
        self.addCleanup(self.tmpdir.cleanup)

        self.session = self.repo.create_session("tenant_a", None, "Tenant A's chat")
        self.sid = self.session["session_id"]

    def _upload_as(self, tenant_id: str, session_id: str):
        with patch.object(chat_sessions_api, "repository", self.repo), \
             patch.object(chat_sessions_api, "chat_attachment_storage", self.storage):
            return asyncio.run(chat_sessions_api.upload_attachment(
                request=_fake_request(), session_id=session_id, file=_make_upload_file(),
                identity=TokenIdentity(tenant_id=tenant_id, role="ADMIN"),
            ))

    def test_upload_to_another_tenants_session_404s(self):
        with self.assertRaises(HTTPException) as cm:
            self._upload_as("tenant_b", self.sid)
        self.assertEqual(cm.exception.status_code, 404)

        # The legitimate owner can still upload to their own session.
        result = self._upload_as("tenant_a", self.sid)
        self.assertEqual(result.mime_type, "image/png")

    def test_retrieve_rejects_wrong_tenant(self):
        uploaded = self._upload_as("tenant_a", self.sid)

        with patch.object(chat_sessions_api, "repository", self.repo), \
             patch.object(chat_sessions_api, "chat_attachment_storage", self.storage):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(chat_sessions_api.get_attachment(
                    session_id=self.sid, attachment_id=uploaded.attachment_id,
                    identity=TokenIdentity(tenant_id="tenant_b", role="ADMIN"),
                ))
            self.assertEqual(cm.exception.status_code, 404)

            # The real owner still gets the real bytes back through the same route.
            response = asyncio.run(chat_sessions_api.get_attachment(
                session_id=self.sid, attachment_id=uploaded.attachment_id,
                identity=TokenIdentity(tenant_id="tenant_a", role="ADMIN"),
            ))
        self.assertEqual(response.body, PNG_1X1)

    def test_retrieve_rejects_a_different_session_of_the_same_tenant(self):
        uploaded = self._upload_as("tenant_a", self.sid)
        other_session = self.repo.create_session("tenant_a", None, "A different chat")

        with patch.object(chat_sessions_api, "repository", self.repo), \
             patch.object(chat_sessions_api, "chat_attachment_storage", self.storage):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(chat_sessions_api.get_attachment(
                    session_id=other_session["session_id"], attachment_id=uploaded.attachment_id,
                    identity=TokenIdentity(tenant_id="tenant_a", role="ADMIN"),
                ))
        self.assertEqual(cm.exception.status_code, 404)

    def test_upload_to_nonexistent_session_404s(self):
        with self.assertRaises(HTTPException) as cm:
            self._upload_as("tenant_a", "SESSION_DOES_NOT_EXIST")
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
