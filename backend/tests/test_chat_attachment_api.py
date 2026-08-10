"""
HTTP-level tests for the chat attachment upload/retrieval endpoints
(ADR-008) - real TestClient round trips, exercising request parsing,
size/format validation, and the endpoint's own rate limit
(CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT), matching test_chat_rate_limiting.py's
pattern. tests/conftest.py's autouse _reset_rate_limit_storage fixture
keeps this file isolated from other test files' rate-limit consumption.
"""

import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.api import chat_sessions as chat_sessions_api
    from backend.infrastructure.chat_attachment_storage import ChatAttachmentStorage
    from backend.shared.middleware.rate_limit import CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT

from backend.tests.conftest import auth_headers
from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph
from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844410000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100dcba79820000"
    "000049454e44ae426082"
)
NOT_AN_IMAGE = b"plain text, not an image at all, just some bytes here"


class _FixedKeyProvider:
    def get_key(self) -> bytes:
        return b"\x11" * 32


class ChatAttachmentApiTests(unittest.TestCase):
    def setUp(self):
        graph = FakeChatAttachmentGraph()
        self.repo = Neo4jChatSessionRepository()
        self.repo.graph = graph
        self.session = self.repo.create_session("tenant_a", None, "Chat with images")
        self.sid = self.session["session_id"]

        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.storage = ChatAttachmentStorage(root=self.tmpdir.name, key_provider=_FixedKeyProvider())

        self._patches = [
            patch.object(chat_sessions_api, "repository", self.repo),
            patch.object(chat_sessions_api, "chat_attachment_storage", self.storage),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(app)
        self.limit = int(CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT.split("/")[0])

    def _upload(self, content=PNG_1X1, filename="photo.png", content_type="image/png"):
        return self.client.post(
            f"/api/chat/sessions/{self.sid}/attachments",
            files={"file": (filename, content, content_type)},
            headers=auth_headers(tenant_id="tenant_a"),
        )

    def test_successful_upload_then_retrieve_round_trip(self):
        upload_response = self._upload()
        self.assertEqual(upload_response.status_code, 201)
        body = upload_response.json()
        self.assertEqual(body["mime_type"], "image/png")
        self.assertEqual(body["size_bytes"], len(PNG_1X1))

        get_response = self.client.get(
            f"/api/chat/sessions/{self.sid}/attachments/{body['attachment_id']}",
            headers=auth_headers(tenant_id="tenant_a"),
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.headers["content-type"], "image/png")
        self.assertEqual(get_response.content, PNG_1X1)
        self.assertEqual(get_response.headers["cache-control"], "private, no-store, max-age=0")
        self.assertEqual(get_response.headers["x-content-type-options"], "nosniff")

    def test_oversized_file_is_rejected(self):
        oversized = PNG_1X1[:8] + (b"\x00" * (5 * 1024 * 1024 + 1))
        response = self._upload(content=oversized)
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.json()["detail"].lower())

    def test_non_image_content_is_rejected_regardless_of_declared_content_type(self):
        """The client can lie about Content-Type - the real bytes are what
        get validated (chat_attachment_storage.detect_image_mime_type)."""
        response = self._upload(content=NOT_AN_IMAGE, filename="fake.png", content_type="image/png")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a supported image format", response.json()["detail"])

    def test_upload_to_nonexistent_session_404s(self):
        response = self.client.post(
            "/api/chat/sessions/SESSION_DOES_NOT_EXIST/attachments",
            files={"file": ("photo.png", PNG_1X1, "image/png")},
            headers=auth_headers(tenant_id="tenant_a"),
        )
        self.assertEqual(response.status_code, 404)

    def test_retrieve_nonexistent_attachment_404s(self):
        response = self.client.get(
            f"/api/chat/sessions/{self.sid}/attachments/ATTACH_DOES_NOT_EXIST",
            headers=auth_headers(tenant_id="tenant_a"),
        )
        self.assertEqual(response.status_code, 404)

    def test_requests_within_the_limit_are_not_rate_limited(self):
        for i in range(self.limit):
            response = self._upload()
            self.assertNotEqual(response.status_code, 429, f"request {i + 1}/{self.limit} was rate limited too early")

    def test_the_request_past_the_limit_gets_a_real_429(self):
        for _ in range(self.limit):
            self._upload()
        response = self._upload()
        self.assertEqual(response.status_code, 429)

    def test_limit_is_scoped_per_tenant(self):
        for _ in range(self.limit):
            self._upload()
        exhausted = self._upload()
        self.assertEqual(exhausted.status_code, 429)

        # A different tenant has no session of its own here, so this call
        # legitimately 404s - the point is it's a 404, not a 429, proving
        # tenant_a's exhausted quota didn't leak onto tenant_b.
        other_tenant_response = self.client.post(
            f"/api/chat/sessions/{self.sid}/attachments",
            files={"file": ("photo.png", PNG_1X1, "image/png")},
            headers=auth_headers(tenant_id="tenant_b"),
        )
        self.assertNotEqual(other_tenant_response.status_code, 429)
        self.assertEqual(other_tenant_response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
