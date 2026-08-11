"""
ADR-008 Stage 2: wiring tests for _build_prompt_message (this codebase's
own image content-block construction), the /api/run/ route's explicit
vision-capability gate, and an end-to-end runner() proof that an
attachment-bearing turn builds the real multimodal HumanMessage and links
the attachment to the persisted message. The cross-provider conversion
guarantee itself is covered separately in
test_chat_image_cross_provider_conversion.py.
"""

import asyncio
import base64
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.main as main
    from backend.infrastructure.chat_attachment_storage import ChatAttachmentStorage
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
    from backend.governance.auth import TokenIdentity

from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844410000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100dcba79820000"
    "000049454e44ae426082"
)


class _FixedKeyProvider:
    def get_key(self) -> bytes:
        return b"\x77" * 32


def _fake_request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/api/run/",
        "headers": [], "client": ("testclient", 123), "server": ("testserver", 80),
        "scheme": "http", "query_string": b"",
    })


class BuildPromptMessageTests(unittest.TestCase):
    def setUp(self):
        graph = FakeChatAttachmentGraph()
        self.repo = Neo4jChatSessionRepository()
        self.repo.graph = graph
        self.session = self.repo.create_session("tenant_a", None, "Image chat")
        self.sid = self.session["session_id"]

        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.storage = ChatAttachmentStorage(root=self.tmpdir.name, key_provider=_FixedKeyProvider())

    def test_no_attachments_returns_plain_string_content_unchanged(self):
        message, loaded = main._build_prompt_message("hello", None, self.repo, "tenant_a", self.sid)
        self.assertEqual(message.content, "hello")
        self.assertEqual(loaded, [])

    def test_no_session_repo_returns_plain_string_even_with_attachment_ids(self):
        """Defensive: attachments always require a session in the route,
        but the helper itself must degrade safely, not crash, if ever
        called without one."""
        message, loaded = main._build_prompt_message("hello", ["ATTACH_1"], None, "tenant_a", None)
        self.assertEqual(message.content, "hello")
        self.assertEqual(loaded, [])

    def test_single_attachment_produces_text_then_image_content_blocks(self):
        storage_key, mime_type = self.storage.store("tenant_a", self.sid, "ATTACH_1", PNG_1X1)
        self.repo.create_attachment(self.sid, "tenant_a", "ATTACH_1", mime_type, len(PNG_1X1), storage_key)

        with patch.object(main, "chat_attachment_storage", self.storage):
            message, loaded = main._build_prompt_message(
                "What is this?", ["ATTACH_1"], self.repo, "tenant_a", self.sid,
            )

        self.assertIsInstance(message.content, list)
        self.assertEqual(message.content[0], {"type": "text", "text": "What is this?"})
        image_block = message.content[1]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(image_block["mime_type"], "image/png")
        self.assertEqual(base64.b64decode(image_block["base64"]), PNG_1X1)
        self.assertEqual(loaded, [{"attachment_id": "ATTACH_1", "mime_type": "image/png"}])

    def test_multiple_attachments_produce_one_image_block_each_in_order(self):
        for i, suffix in enumerate(["1", "2"]):
            key, mime = self.storage.store("tenant_a", self.sid, f"ATTACH_{suffix}", PNG_1X1)
            self.repo.create_attachment(self.sid, "tenant_a", f"ATTACH_{suffix}", mime, len(PNG_1X1), key)

        with patch.object(main, "chat_attachment_storage", self.storage):
            message, loaded = main._build_prompt_message(
                "Compare these", ["ATTACH_1", "ATTACH_2"], self.repo, "tenant_a", self.sid,
            )

        self.assertEqual(len(message.content), 3)  # 1 text + 2 image blocks
        self.assertEqual([b["type"] for b in message.content], ["text", "image", "image"])
        self.assertEqual([a["attachment_id"] for a in loaded], ["ATTACH_1", "ATTACH_2"])

    def test_a_vanished_attachment_is_skipped_not_fatal(self):
        """Already ownership-checked in the route before streaming started
        - reaching here with a missing attachment means a narrow race, not
        a caller error. The turn should still proceed with the text and
        whatever attachments do exist."""
        message, loaded = main._build_prompt_message(
            "What is this?", ["ATTACH_DOES_NOT_EXIST"], self.repo, "tenant_a", self.sid,
        )
        self.assertEqual(message.content, [{"type": "text", "text": "What is this?"}])
        self.assertEqual(loaded, [])


class VisionCapabilityGateRouteTests(unittest.TestCase):
    """Drives the real run() route directly, mocking validate_model to
    return a spec without "vision" - tests the gate logic in isolation
    from which real providers happen to be configured in this environment
    (mistral-large, the one real non-vision spec, isn't configured by
    default and would otherwise fail earlier on an unrelated check)."""

    def setUp(self):
        self.identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN")
        self.llm_mgr = MagicMock()
        self.llm_mgr.agents = {"non-vision-model": MagicMock()}

        graph = FakeChatAttachmentGraph()
        self.repo = Neo4jChatSessionRepository()
        self.repo.graph = graph
        self.session = self.repo.create_session("tenant_a", None, "Image chat")
        self.sid = self.session["session_id"]
        self.repo.create_attachment(self.sid, "tenant_a", "ATTACH_1", "image/png", 1, "a" * 64)

        self.non_vision_spec = MagicMock()
        self.non_vision_spec.provider = "mistral"
        self.non_vision_spec.capabilities = frozenset({"chat", "tool_calling", "streaming"})

        self.vision_spec = MagicMock()
        self.vision_spec.provider = "google"
        self.vision_spec.capabilities = frozenset({"chat", "tool_calling", "streaming", "vision"})

    def _payload(self, attachment_ids=("ATTACH_1",)):
        return main.RunPayload(
            model="non-vision-model", prompt="What is in this image?",
            session_id=self.sid, attachment_ids=list(attachment_ids) if attachment_ids else None,
        )

    def test_non_vision_model_with_attachment_is_rejected_before_any_provider_call(self):
        with patch.object(main, "validate_model", return_value=self.non_vision_spec), \
             patch.object(main, "Neo4jChatSessionRepository", return_value=self.repo), \
             patch.object(main, "resilient_runner") as resilient_runner, \
             patch.object(main, "runner") as runner:
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(main.run(_fake_request(), self._payload(), llm_mgr=self.llm_mgr, identity=self.identity))

        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(cm.exception.detail["category"], "vision_unsupported")
        # The whole point of gating in the route: no generation call of any
        # kind was ever started.
        resilient_runner.assert_not_called()
        runner.assert_not_called()

    def test_vision_capable_model_with_the_same_attachment_is_allowed_through(self):
        with patch.object(main, "validate_model", return_value=self.vision_spec), \
             patch.object(main, "Neo4jChatSessionRepository", return_value=self.repo):
            response = asyncio.run(main.run(_fake_request(), self._payload(), llm_mgr=self.llm_mgr, identity=self.identity))

        self.assertEqual(response.media_type, "text/event-stream")

    def test_non_vision_model_without_any_attachment_is_unaffected(self):
        """The gate must only fire when there's actually an attachment -
        a non-vision model must remain perfectly usable for plain text."""
        with patch.object(main, "validate_model", return_value=self.non_vision_spec), \
             patch.object(main, "Neo4jChatSessionRepository", return_value=self.repo):
            response = asyncio.run(main.run(
                _fake_request(), self._payload(attachment_ids=None),
                llm_mgr=self.llm_mgr, identity=self.identity,
            ))
        self.assertEqual(response.media_type, "text/event-stream")

    def test_more_than_four_attachments_is_rejected_before_any_provider_call(self):
        """ADR-008: bounds cost/context blowup per turn. Real gap found
        during Stage 3 verification - the limit was documented in the ADR
        but never actually enforced in code."""
        for i in range(2, 6):
            self.repo.create_attachment(self.sid, "tenant_a", f"ATTACH_{i}", "image/png", 1, "a" * 64)
        payload = self._payload(attachment_ids=[f"ATTACH_{i}" for i in range(1, 6)])  # 5 total

        with patch.object(main, "validate_model", return_value=self.vision_spec), \
             patch.object(main, "Neo4jChatSessionRepository", return_value=self.repo), \
             patch.object(main, "resilient_runner") as resilient_runner, \
             patch.object(main, "runner") as runner:
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(main.run(_fake_request(), payload, llm_mgr=self.llm_mgr, identity=self.identity))

        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("4", cm.exception.detail)
        resilient_runner.assert_not_called()
        runner.assert_not_called()

    def test_exactly_four_attachments_is_allowed(self):
        for i in range(2, 5):
            self.repo.create_attachment(self.sid, "tenant_a", f"ATTACH_{i}", "image/png", 1, "a" * 64)
        payload = self._payload(attachment_ids=[f"ATTACH_{i}" for i in range(1, 5)])  # 4 total

        with patch.object(main, "validate_model", return_value=self.vision_spec), \
             patch.object(main, "Neo4jChatSessionRepository", return_value=self.repo):
            response = asyncio.run(main.run(_fake_request(), payload, llm_mgr=self.llm_mgr, identity=self.identity))

        self.assertEqual(response.media_type, "text/event-stream")

    def test_attachments_without_a_session_id_are_rejected(self):
        payload = main.RunPayload(model="non-vision-model", prompt="hi", attachment_ids=["ATTACH_1"])
        with patch.object(main, "validate_model", return_value=self.vision_spec):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(main.run(_fake_request(), payload, llm_mgr=self.llm_mgr, identity=self.identity))
        self.assertEqual(cm.exception.status_code, 400)

    def test_an_attachment_belonging_to_another_tenant_404s(self):
        with patch.object(main, "validate_model", return_value=self.vision_spec), \
             patch.object(main, "Neo4jChatSessionRepository", return_value=self.repo):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(main.run(
                    _fake_request(), self._payload(),
                    llm_mgr=self.llm_mgr,
                    identity=TokenIdentity(tenant_id="tenant_b", role="ADMIN"),
                ))
        self.assertEqual(cm.exception.status_code, 404)


class RunnerEndToEndImageTurnTests(unittest.IsolatedAsyncioTestCase):
    """Real runner() end to end (fake LLM, real repository/storage against
    an in-memory graph + tmp-dir storage), matching
    test_chat_run_session_persistence.py's pattern - proving an
    attachment-bearing turn both reaches the model as the real multimodal
    content and gets linked to the persisted message afterward."""

    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_image_reaches_the_model_and_gets_linked_to_the_persisted_message(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        session = repo.create_session("tenant_a", None, "Image chat")
        sid = session["session_id"]

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ChatAttachmentStorage(root=tmpdir, key_provider=_FixedKeyProvider())
            storage_key, mime_type = storage.store("tenant_a", sid, "ATTACH_1", PNG_1X1)
            repo.create_attachment(sid, "tenant_a", "ATTACH_1", mime_type, len(PNG_1X1), storage_key)

            captured = {}

            async def capturing_astream(*args, **kwargs):
                captured["input_messages"] = kwargs["input"]["messages"]
                from langchain_core.messages import AIMessage, AIMessageChunk
                final_chunk = AIMessageChunk(content="This is a small red square.")
                final_assistant = AIMessage(content="This is a small red square.", tool_calls=[])
                yield ("messages", (final_chunk, {}))
                yield ("updates", {"assistant": {"messages": [final_assistant]}})

            fake_model = MagicMock()
            fake_model.astream = capturing_astream
            fake_llm_mgr = MagicMock()
            fake_llm_mgr.get_model_by_name.return_value = fake_model

            with patch.object(main, "Neo4jChatSessionRepository", return_value=repo), \
                 patch.object(main, "chat_attachment_storage", storage), \
                 patch("backend.main.PromptGuard") as MockGuard, \
                 patch("backend.main.OutputGuard") as MockOutputGuard, \
                 patch("backend.main.AuditLogger"), \
                 patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
                MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)
                MockOutputGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, metadata={})

                await self._collect(main.runner(
                    model="gemini-2.5-flash", prompt="What is this?", history="[]",
                    llm_mgr=fake_llm_mgr, tenant_id="tenant_a", chat_session_id=sid,
                    attachment_ids=["ATTACH_1"],
                ))

        # The model actually received the real multimodal content.
        sent_content = captured["input_messages"][-1].content
        self.assertIsInstance(sent_content, list)
        self.assertEqual(sent_content[0], {"type": "text", "text": "What is this?"})
        self.assertEqual(base64.b64decode(sent_content[1]["base64"]), PNG_1X1)
        self.assertEqual(sent_content[1]["mime_type"], "image/png")

        # The attachment is linked to the persisted user_message row.
        messages = repo.list_messages(sid, "tenant_a")
        user_message = next(m for m in messages if m["role"] == "user_message")
        linked = repo.list_attachments_for_message(user_message["message_id"], "tenant_a")
        self.assertEqual([a["attachment_id"] for a in linked], ["ATTACH_1"])

        # And the persisted content itself stays plain text - the
        # attachment relationship is the only record of the image, per
        # ADR-008.
        self.assertEqual(user_message["content"], "What is this?")

    async def test_history_sse_event_omits_the_raw_base64_image_payload(self):
        """Real, confirmed bug: the terminal "history" SSE event echoed
        the full raw image data back to the client for zero benefit -
        input.tsx's onmessage handler no-ops on type "history" entirely
        now that session persistence is authoritative. This proves the
        fix (_history_safe_json) actually reaches that real event, not
        just the helper in isolation."""
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        session = repo.create_session("tenant_a", None, "Image chat")
        sid = session["session_id"]

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ChatAttachmentStorage(root=tmpdir, key_provider=_FixedKeyProvider())
            storage_key, mime_type = storage.store("tenant_a", sid, "ATTACH_1", PNG_1X1)
            repo.create_attachment(sid, "tenant_a", "ATTACH_1", mime_type, len(PNG_1X1), storage_key)

            async def capturing_astream(*args, **kwargs):
                from langchain_core.messages import AIMessage, AIMessageChunk
                final_chunk = AIMessageChunk(content="This is a small red square.")
                final_assistant = AIMessage(content="This is a small red square.", tool_calls=[])
                yield ("messages", (final_chunk, {}))
                yield ("updates", {"assistant": {"messages": [final_assistant]}})

            fake_model = MagicMock()
            fake_model.astream = capturing_astream
            fake_llm_mgr = MagicMock()
            fake_llm_mgr.get_model_by_name.return_value = fake_model

            with patch.object(main, "Neo4jChatSessionRepository", return_value=repo), \
                 patch.object(main, "chat_attachment_storage", storage), \
                 patch("backend.main.PromptGuard") as MockGuard, \
                 patch("backend.main.OutputGuard") as MockOutputGuard, \
                 patch("backend.main.AuditLogger"), \
                 patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
                MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)
                MockOutputGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, metadata={})

                events = await self._collect(main.runner(
                    model="gemini-2.5-flash", prompt="What is this?", history="[]",
                    llm_mgr=fake_llm_mgr, tenant_id="tenant_a", chat_session_id=sid,
                    attachment_ids=["ATTACH_1"],
                ))

        raw_b64 = base64.b64encode(PNG_1X1).decode("ascii")
        history_events = [e for e in events if json.loads(e.removeprefix("data: ").strip())["type"] == "history"]
        self.assertEqual(len(history_events), 1)
        history_payload = json.loads(history_events[0].removeprefix("data: ").strip())

        # The raw image bytes never appear anywhere in the emitted event...
        self.assertNotIn(raw_b64, json.dumps(history_payload))
        # ...but the human turn's own entry is still present, with its
        # non-image metadata intact (text content, mime_type).
        human_entries = [
            json.loads(item) for item in history_payload["content"]
            if json.loads(item).get("type") == "human"
        ]
        self.assertEqual(len(human_entries), 1)
        blocks = human_entries[0]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "What is this?"})
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["mime_type"], "image/png")
        self.assertEqual(blocks[1]["base64"], "[omitted]")


if __name__ == "__main__":
    unittest.main()
