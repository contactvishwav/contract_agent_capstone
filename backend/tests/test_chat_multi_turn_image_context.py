"""
Real, confirmed bugs found live during manual browser testing of Stage 3
(image attachments), investigated and fixed as a pre-Stage-4 pass since
Stage 4's quote-reply builds on the same session-history reconstruction
mechanism:

1. contract_chat_agent.py's assistant node forced an EnhancedContractSearch
   call unconditionally whenever the model didn't call a tool itself - with
   no concept of attachments, this fired even for a turn whose only real
   evidence need was an attached image (e.g. "what page is this image
   from?"), discarding the model's real, direct examination of the image
   and replacing it with a confused, irrelevant contract search. Fixed via
   _conversation_has_image_evidence: an attached image is real evidence
   (same principle as ADR-004's image_attachment evidence type), so a turn
   that has one needs no forced retrieval just because the model chose not
   to call a tool.

2. Session-history replay (_messages_from_stored) never carried any trace
   of an attachment into later turns - persisted content is always plain
   text (ADR-008 design: only the HAS_ATTACHMENT relationship records the
   image). A follow-up question referring to an image from an earlier turn
   therefore had no way for the model to know the image had ever existed.

ADR-008 follow-up (this pass): (1) and (2) above were both deliberately
scoped to "current turn only" / "marker only, never real bytes" - real,
live consequence: attach an image, ask about it, get a correct answer -
then in a brand-new message with NO re-attachment, ask a natural
follow-up ("what color is the circle?"). The model was told point-blank
it could no longer see the image and had to decline, which is not what a
real conversation should do. Fixed with a bounded carry-forward: the
SINGLE most recent image-bearing turn's real image bytes are re-loaded
and attached as real content blocks in every later turn, until a NEW
image attachment supersedes it (at which point IT becomes "the most
recent" and the older one reverts to the marker). Any image-bearing turn
OLDER than the single most recent one still gets exactly the marker-only
treatment from before - this is bounded, constant added cost per turn
(at most one turn's worth of images), not unbounded replay of the whole
session's attachment history. _conversation_has_image_evidence's scan
was broadened to match: it no longer only looks at the latest
HumanMessage, since the carried-forward image now lives on an earlier
one once even one more turn has happened since it was attached.
"""

import base64
import tempfile
import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.contract_chat_agent import _conversation_has_image_evidence, get_agent
    import backend.main as main
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
    from backend.infrastructure.chat_attachment_storage import ChatAttachmentStorage

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844410000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100dcba79820000"
    "000049454e44ae426082"
)


class _FixedKeyProvider:
    def get_key(self) -> bytes:
        return b"\x77" * 32


class ConversationHasImageEvidenceTests(unittest.TestCase):
    def test_true_when_latest_human_message_has_an_image_block(self):
        messages = [HumanMessage(content=[
            {"type": "text", "text": "What is this?"},
            {"type": "image", "base64": "abc", "mime_type": "image/png"},
        ])]
        self.assertTrue(_conversation_has_image_evidence(messages))

    def test_false_for_plain_string_content(self):
        self.assertFalse(_conversation_has_image_evidence([HumanMessage(content="hello")]))

    def test_false_for_list_content_with_only_a_text_block(self):
        messages = [HumanMessage(content=[{"type": "text", "text": "hello"}])]
        self.assertFalse(_conversation_has_image_evidence(messages))

    def test_false_with_no_messages(self):
        self.assertFalse(_conversation_has_image_evidence([]))

    def test_true_for_an_earlier_human_message_not_just_the_latest(self):
        """ADR-008 follow-up: broadened from "only the latest HumanMessage
        counts" (the original, narrower behavior) - an earlier turn's
        carried-forward image must now be recognized too, since a genuine
        follow-up about it is no longer necessarily the very last message
        once even one more turn has passed since it was attached."""
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "Describe this"},
                {"type": "image", "base64": "abc", "mime_type": "image/png"},
            ]),
            AIMessage(content="I see a circle."),
            HumanMessage(content="What color is it?"),
        ]
        self.assertTrue(_conversation_has_image_evidence(messages))

    def test_true_even_with_prior_ai_and_tool_messages_in_between(self):
        messages = [
            ToolMessage(content="irrelevant prior tool result", tool_call_id="x"),
            HumanMessage(content=[
                {"type": "image", "base64": "abc", "mime_type": "image/png"},
            ]),
        ]
        self.assertTrue(_conversation_has_image_evidence(messages))


class AssistantNodeSkipsForcingForImageTurnsTests(unittest.TestCase):
    """End-to-end through the real compiled graph (get_agent), matching
    test_chat_image_message_text_extraction.py's pattern."""

    def test_image_turn_with_a_direct_model_answer_never_forces_a_tool_call(self):
        fake_llm = MagicMock()
        fake_llm.bind_tools.return_value = fake_llm
        fake_llm.invoke.return_value = AIMessage(content="This shows a blue circle on a white background.")

        image_message = HumanMessage(content=[
            {"type": "text", "text": "What page is this image from?"},
            {"type": "image", "base64": "abc", "mime_type": "image/png"},
        ])

        with patch(
            "backend.shared.utils.enhanced_contract_search_tool.EnhancedContractSearchTool._run",
        ) as fake_run:
            graph = get_agent(fake_llm)
            result = graph.invoke(
                {"messages": [image_message]},
                config={"configurable": {"tenant_id": "tenant_a"}},
            )

        fake_run.assert_not_called()
        self.assertEqual(
            result["messages"][-1].content,
            "This shows a blue circle on a white background.",
        )

    def test_plain_text_turn_with_no_image_still_forces_a_tool_call_as_before(self):
        """Regression guard: the fix must not weaken the pre-existing
        safety net for ordinary text-only questions."""
        fake_llm = MagicMock()
        fake_llm.bind_tools.return_value = fake_llm
        fake_llm.invoke.return_value = AIMessage(content="Payment is due within 90 days.")

        with patch(
            "backend.shared.utils.enhanced_contract_search_tool.EnhancedContractSearchTool._run",
            return_value={"result": {"total_count": 0, "contracts": []}},
        ) as fake_run, patch(
            "backend.contract_chat_agent.build_evidence_envelope",
            return_value={
                "schema_version": "chat-evidence-v1", "tenant_id": "tenant_a",
                "tool_name": "EnhancedContractSearch", "tool_call_id": "forced_test", "evidence": [],
            },
        ):
            graph = get_agent(fake_llm)
            graph.invoke(
                {"messages": [HumanMessage(content="What are the payment terms?")]},
                config={"configurable": {"tenant_id": "tenant_a"}},
            )

        fake_run.assert_called_once()


class ListMessagesReportsHasAttachmentTests(unittest.TestCase):
    def _repo(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        return repo

    def test_user_message_with_a_linked_attachment_reports_true(self):
        repo = self._repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        msg = repo.append_message(sid, "tenant_a", role="user_message", content="What is this?")
        repo.create_attachment(sid, "tenant_a", "ATTACH_1", "image/png", 10, "a" * 64)
        repo.link_attachment_to_message("ATTACH_1", msg["message_id"], "tenant_a")

        rows = repo.list_messages(sid, "tenant_a")
        self.assertTrue(rows[0]["has_attachment"])

    def test_user_message_without_any_attachment_reports_false(self):
        repo = self._repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        repo.append_message(sid, "tenant_a", role="user_message", content="Hello")

        rows = repo.list_messages(sid, "tenant_a")
        self.assertFalse(rows[0]["has_attachment"])


class MessagesFromStoredCarriesTheMostRecentImageForwardTests(unittest.TestCase):
    """_messages_from_stored's own carry-forward decision, in isolation
    (a real repo/storage, but hand-built stored_messages rows rather than
    driving the full runner()) - RunnerCarriesTheMostRecentImageForward
    EndToEndTests below proves the same behavior through the real
    pipeline."""

    def _repo_and_storage(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        storage = ChatAttachmentStorage(root=tmpdir, key_provider=_FixedKeyProvider())
        return repo, storage

    def test_the_single_most_recent_image_bearing_row_gets_real_content_blocks(self):
        repo, storage = self._repo_and_storage()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        msg = repo.append_message(sid, "tenant_a", role="user_message", content="What is this?")
        storage_key, mime_type = storage.store("tenant_a", sid, "ATTACH_1", PNG_1X1)
        repo.create_attachment(sid, "tenant_a", "ATTACH_1", mime_type, len(PNG_1X1), storage_key)
        repo.link_attachment_to_message("ATTACH_1", msg["message_id"], "tenant_a")

        stored_messages = repo.list_messages(sid, "tenant_a")
        with patch.object(main, "chat_attachment_storage", storage):
            messages, carried_forward = main._messages_from_stored(stored_messages, repo, "tenant_a", sid)

        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0].content, list)
        self.assertEqual(messages[0].content[0], {"type": "text", "text": "What is this?"})
        image_block = messages[0].content[1]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(base64.b64decode(image_block["base64"]), PNG_1X1)
        self.assertEqual(carried_forward, [{"attachment_id": "ATTACH_1", "mime_type": "image/png"}])

    def test_an_older_image_bearing_row_gets_the_marker_once_superseded(self):
        repo, storage = self._repo_and_storage()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]

        first_msg = repo.append_message(sid, "tenant_a", role="user_message", content="What is in image A?")
        key_a, mime_a = storage.store("tenant_a", sid, "ATTACH_A", PNG_1X1)
        repo.create_attachment(sid, "tenant_a", "ATTACH_A", mime_a, len(PNG_1X1), key_a)
        repo.link_attachment_to_message("ATTACH_A", first_msg["message_id"], "tenant_a")
        repo.append_message(sid, "tenant_a", role="ai_message", content="Image A shows a square.")

        second_msg = repo.append_message(sid, "tenant_a", role="user_message", content="What is in image B?")
        key_b, mime_b = storage.store("tenant_a", sid, "ATTACH_B", PNG_1X1)
        repo.create_attachment(sid, "tenant_a", "ATTACH_B", mime_b, len(PNG_1X1), key_b)
        repo.link_attachment_to_message("ATTACH_B", second_msg["message_id"], "tenant_a")

        stored_messages = repo.list_messages(sid, "tenant_a")
        with patch.object(main, "chat_attachment_storage", storage):
            messages, carried_forward = main._messages_from_stored(stored_messages, repo, "tenant_a", sid)

        # The OLDER row (image A) is superseded by the newer one (image B)
        # - marker only, real bytes never re-loaded for it.
        first_human = messages[0]
        self.assertIsInstance(first_human.content, str)
        self.assertIn("What is in image A?", first_human.content)
        self.assertIn("not available in the current turn", first_human.content)

        # The MOST RECENT row (image B) gets the real content blocks.
        last_human = messages[-1]
        self.assertIsInstance(last_human.content, list)
        self.assertEqual(last_human.content[0], {"type": "text", "text": "What is in image B?"})
        self.assertEqual(last_human.content[1]["type"], "image")
        self.assertEqual(carried_forward, [{"attachment_id": "ATTACH_B", "mime_type": "image/png"}])

    def test_user_message_row_without_attachment_has_plain_content(self):
        messages, carried_forward = main._messages_from_stored([
            {"role": "user_message", "content": "Hello", "has_attachment": False},
        ])
        self.assertEqual(messages[0].content, "Hello")
        self.assertEqual(carried_forward, [])

    def test_ai_message_row_is_never_marked_even_if_has_attachment_is_somehow_set(self):
        """Defensive: attachments only ever link to user_message rows
        (chat_sessions.py's get_session_detail already scopes it that way),
        but _messages_from_stored must not blindly trust an unexpected
        shape either."""
        messages, carried_forward = main._messages_from_stored([
            {"role": "ai_message", "content": "Here is the answer.", "has_attachment": True},
        ])
        self.assertEqual(messages[0].content, "Here is the answer.")
        self.assertEqual(carried_forward, [])

    def test_missing_has_attachment_key_defaults_to_no_marker(self):
        """Backward-compatible: rows from a repository call that doesn't
        populate has_attachment must not crash or spuriously mark."""
        messages, carried_forward = main._messages_from_stored([
            {"role": "user_message", "content": "Hello"},
        ])
        self.assertEqual(messages[0].content, "Hello")
        self.assertEqual(carried_forward, [])

    def test_no_chat_session_repo_degrades_to_the_marker(self):
        """Callers that don't pass a repo (e.g. simple unit tests, or any
        future caller with no attachment access) must still degrade
        gracefully to the marker, never crash."""
        messages, carried_forward = main._messages_from_stored([
            {"role": "user_message", "content": "What is this?", "message_id": "M1", "has_attachment": True},
        ])
        self.assertIn("not available in the current turn", messages[0].content)
        self.assertEqual(carried_forward, [])

    def test_a_vanished_attachment_degrades_to_the_marker_not_a_crash(self):
        """Mirrors _build_prompt_message's own vanished-attachment
        handling: has_attachment was true when the row was written, but
        the attachment is no longer reachable (e.g. deleted since) by the
        time this later turn processes it."""
        repo, storage = self._repo_and_storage()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        msg = repo.append_message(sid, "tenant_a", role="user_message", content="What is this?")
        # has_attachment=True is asserted directly (bypassing a real
        # create_attachment/link) to simulate the attachment having since
        # vanished - list_attachments_for_message will legitimately find
        # nothing to load.
        stored_messages = [{
            "role": "user_message", "content": "What is this?",
            "message_id": msg["message_id"], "has_attachment": True,
        }]

        with patch.object(main, "chat_attachment_storage", storage):
            messages, carried_forward = main._messages_from_stored(stored_messages, repo, "tenant_a", sid)

        self.assertIn("not available in the current turn", messages[0].content)
        self.assertEqual(carried_forward, [])


class RunnerCarriesTheMostRecentImageForwardEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Real runner() end to end - proves a follow-up turn's model actually
    receives the real carried-forward image (not just that the helper
    functions produce it in isolation), matching
    test_chat_image_attachment_wiring.py's RunnerEndToEndImageTurnTests
    pattern."""

    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_followup_turn_without_reattachment_receives_the_real_image_and_grounds_it(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        session = repo.create_session("tenant_a", None, "Image chat")
        sid = session["session_id"]

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ChatAttachmentStorage(root=tmpdir, key_provider=_FixedKeyProvider())
            storage_key, mime_type = storage.store("tenant_a", sid, "ATTACH_1", PNG_1X1)
            repo.create_attachment(sid, "tenant_a", "ATTACH_1", mime_type, len(PNG_1X1), storage_key)

            # Turn 1: a prior user_message with a linked attachment,
            # already persisted (simulating a completed first turn).
            first_message = repo.append_message(sid, "tenant_a", role="user_message", content="What's in this image?")
            repo.link_attachment_to_message("ATTACH_1", first_message["message_id"], "tenant_a")
            repo.append_message(sid, "tenant_a", role="ai_message", content="A blue circle and an orange square.")

            captured = {}

            async def capturing_astream(*args, **kwargs):
                captured["input_messages"] = kwargs["input"]["messages"]
                from langchain_core.messages import AIMessage as _AIMessage, AIMessageChunk
                final_chunk = AIMessageChunk(content="The circle is blue.")
                final_assistant = _AIMessage(content="The circle is blue.", tool_calls=[])
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

                # Turn 2: the follow-up, no attachment_ids this time.
                await self._collect(main.runner(
                    model="gemini-2.5-flash", prompt="What color is it?",
                    history="[]", llm_mgr=fake_llm_mgr, tenant_id="tenant_a", chat_session_id=sid,
                ))

        # Turn 2's model actually received turn 1's real image content -
        # not the marker, not silence.
        history_messages = captured["input_messages"][:-1]  # exclude turn 2's own new prompt
        image_bearing = [m for m in history_messages if isinstance(m.content, list)]
        self.assertEqual(len(image_bearing), 1, f"expected exactly one real image-bearing message, got: {history_messages}")
        image_blocks = [b for b in image_bearing[0].content if isinstance(b, dict) and b.get("type") == "image"]
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(base64.b64decode(image_blocks[0]["base64"]), PNG_1X1)

        # And the carried-forward image is grounded as image_attachment
        # evidence for THIS turn's Output Guard check too.
        output_guard_call = MockOutputGuard.return_value.validate.call_args
        context_metadata = output_guard_call.kwargs.get("context_metadata") or output_guard_call.args[1]
        evidence_types = {item["source_type"] for item in context_metadata["evidence_envelope"]["evidence"]}
        self.assertIn("image_attachment", evidence_types)

    async def test_a_new_attachment_supersedes_the_old_one_which_reverts_to_the_marker(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        session = repo.create_session("tenant_a", None, "Image chat")
        sid = session["session_id"]

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ChatAttachmentStorage(root=tmpdir, key_provider=_FixedKeyProvider())

            # Turn 1: image A.
            key_a, mime_a = storage.store("tenant_a", sid, "ATTACH_A", PNG_1X1)
            repo.create_attachment(sid, "tenant_a", "ATTACH_A", mime_a, len(PNG_1X1), key_a)
            msg_a = repo.append_message(sid, "tenant_a", role="user_message", content="What's in image A?")
            repo.link_attachment_to_message("ATTACH_A", msg_a["message_id"], "tenant_a")
            repo.append_message(sid, "tenant_a", role="ai_message", content="Image A shows a square.")

            # Turn 2: image B - a NEW attachment, superseding image A.
            key_b, mime_b = storage.store("tenant_a", sid, "ATTACH_B", PNG_1X1)
            repo.create_attachment(sid, "tenant_a", "ATTACH_B", mime_b, len(PNG_1X1), key_b)
            msg_b = repo.append_message(sid, "tenant_a", role="user_message", content="What's in image B?")
            repo.link_attachment_to_message("ATTACH_B", msg_b["message_id"], "tenant_a")
            repo.append_message(sid, "tenant_a", role="ai_message", content="Image B shows a triangle.")

            captured = {}

            async def capturing_astream(*args, **kwargs):
                captured["input_messages"] = kwargs["input"]["messages"]
                from langchain_core.messages import AIMessage as _AIMessage, AIMessageChunk
                final_chunk = AIMessageChunk(content="It's a triangle.")
                final_assistant = _AIMessage(content="It's a triangle.", tool_calls=[])
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

                # Turn 3: a follow-up about the FIRST image, no re-attachment.
                await self._collect(main.runner(
                    model="gemini-2.5-flash", prompt="What shape was in the first image again?",
                    history="[]", llm_mgr=fake_llm_mgr, tenant_id="tenant_a", chat_session_id=sid,
                ))

        history_messages = captured["input_messages"][:-1]
        image_bearing = [m for m in history_messages if isinstance(m.content, list)]
        # Only image B (the most recent) is real; image A is marker-only.
        self.assertEqual(len(image_bearing), 1, f"expected exactly one real image-bearing message, got: {history_messages}")

        marker_bearing = [
            m for m in history_messages
            if isinstance(m.content, str) and "not available in the current turn" in m.content
        ]
        self.assertEqual(len(marker_bearing), 1)
        self.assertIn("What's in image A?", marker_bearing[0].content)


if __name__ == "__main__":
    unittest.main()
