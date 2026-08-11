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
   _current_turn_has_image: an attached image is real evidence (same
   principle as ADR-004's image_attachment evidence type), so a turn that
   has one needs no forced retrieval just because the model chose not to
   call a tool.

2. Session-history replay (_messages_from_stored) never carried any trace
   of an attachment into later turns - persisted content is always plain
   text (ADR-008 design: only the HAS_ATTACHMENT relationship records the
   image). A follow-up question referring to an image from an earlier turn
   therefore had no way for the model to know the image had ever existed,
   producing either a hallucinated description or - once evidence grounding
   correctly caught the hallucination - a confusing Output Guard rejection.
   Deliberate design decision (matching this codebase's existing restraint
   for tool evidence - see _messages_from_stored's own docstring): images
   stay in-context for the turn they were attached to only, not replayed
   into every later turn (unbounded cost/context growth for a real
   architectural tradeoff outside this pass's scope). Fixed by having
   list_messages report has_attachment per row and _messages_from_stored
   append a plain-text marker so a later turn's model can honestly decline
   ("I don't have access to that image anymore") instead of guessing or
   getting confused into another irrelevant forced tool call.
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.contract_chat_agent import _current_turn_has_image, get_agent
    import backend.main as main
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph


class CurrentTurnHasImageTests(unittest.TestCase):
    def test_true_when_latest_human_message_has_an_image_block(self):
        messages = [HumanMessage(content=[
            {"type": "text", "text": "What is this?"},
            {"type": "image", "base64": "abc", "mime_type": "image/png"},
        ])]
        self.assertTrue(_current_turn_has_image(messages))

    def test_false_for_plain_string_content(self):
        self.assertFalse(_current_turn_has_image([HumanMessage(content="hello")]))

    def test_false_for_list_content_with_only_a_text_block(self):
        messages = [HumanMessage(content=[{"type": "text", "text": "hello"}])]
        self.assertFalse(_current_turn_has_image(messages))

    def test_false_with_no_messages(self):
        self.assertFalse(_current_turn_has_image([]))

    def test_only_the_latest_human_message_counts_not_an_earlier_one(self):
        """Matches _current_turn_has_tool_evidence's own scoping - an image
        attached several turns ago must not make every later turn look
        like it has current image evidence."""
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "Describe this"},
                {"type": "image", "base64": "abc", "mime_type": "image/png"},
            ]),
            AIMessage(content="I see a circle."),
            HumanMessage(content="What did you just say?"),
        ]
        self.assertFalse(_current_turn_has_image(messages))

    def test_true_even_with_prior_ai_and_tool_messages_in_between(self):
        messages = [
            ToolMessage(content="irrelevant prior tool result", tool_call_id="x"),
            HumanMessage(content=[
                {"type": "image", "base64": "abc", "mime_type": "image/png"},
            ]),
        ]
        self.assertTrue(_current_turn_has_image(messages))


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


class MessagesFromStoredHistoricalImageMarkerTests(unittest.TestCase):
    def test_user_message_row_with_attachment_gets_the_marker_appended(self):
        messages = main._messages_from_stored([
            {"role": "user_message", "content": "What is this?", "has_attachment": True},
        ])
        self.assertIn("What is this?", messages[0].content)
        self.assertIn("not available in the current turn", messages[0].content)

    def test_user_message_row_without_attachment_has_plain_content(self):
        messages = main._messages_from_stored([
            {"role": "user_message", "content": "Hello", "has_attachment": False},
        ])
        self.assertEqual(messages[0].content, "Hello")

    def test_ai_message_row_is_never_marked_even_if_has_attachment_is_somehow_set(self):
        """Defensive: attachments only ever link to user_message rows
        (chat_sessions.py's get_session_detail already scopes it that way),
        but _messages_from_stored must not blindly trust an unexpected
        shape either."""
        messages = main._messages_from_stored([
            {"role": "ai_message", "content": "Here is the answer.", "has_attachment": True},
        ])
        self.assertEqual(messages[0].content, "Here is the answer.")

    def test_missing_has_attachment_key_defaults_to_no_marker(self):
        """Backward-compatible: rows from a repository call that doesn't
        populate has_attachment must not crash or spuriously mark."""
        messages = main._messages_from_stored([
            {"role": "user_message", "content": "Hello"},
        ])
        self.assertEqual(messages[0].content, "Hello")


class RunnerHonorsTheHistoricalMarkerEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Real runner() end to end - proves a follow-up turn's model actually
    receives the marker (not just that the helper functions produce it in
    isolation), matching test_chat_image_attachment_wiring.py's
    RunnerEndToEndImageTurnTests pattern."""

    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_followup_turn_without_reattachment_sees_the_marker_not_the_image(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        session = repo.create_session("tenant_a", None, "Image chat")
        sid = session["session_id"]

        # Turn 1: a prior user_message with a linked attachment, already
        # persisted (simulating a completed first turn).
        first_message = repo.append_message(sid, "tenant_a", role="user_message", content="What page is this from?")
        repo.create_attachment(sid, "tenant_a", "ATTACH_1", "image/png", 10, "a" * 64)
        repo.link_attachment_to_message("ATTACH_1", first_message["message_id"], "tenant_a")
        repo.append_message(sid, "tenant_a", role="ai_message", content="This appears to be page 4.")

        captured = {}

        async def capturing_astream(*args, **kwargs):
            captured["input_messages"] = kwargs["input"]["messages"]
            from langchain_core.messages import AIMessage as _AIMessage, AIMessageChunk
            final_chunk = AIMessageChunk(content="I don't have access to that image anymore - please re-attach it.")
            final_assistant = _AIMessage(content="I don't have access to that image anymore - please re-attach it.", tool_calls=[])
            yield ("messages", (final_chunk, {}))
            yield ("updates", {"assistant": {"messages": [final_assistant]}})

        fake_model = MagicMock()
        fake_model.astream = capturing_astream
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.get_model_by_name.return_value = fake_model

        with patch.object(main, "Neo4jChatSessionRepository", return_value=repo), \
             patch("backend.main.PromptGuard") as MockGuard, \
             patch("backend.main.OutputGuard") as MockOutputGuard, \
             patch("backend.main.AuditLogger"), \
             patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
            MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)
            MockOutputGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, metadata={})

            # Turn 2: the follow-up, no attachment_ids this time.
            await self._collect(main.runner(
                model="gemini-2.5-flash", prompt="What's the content from the image you see then?",
                history="[]", llm_mgr=fake_llm_mgr, tenant_id="tenant_a", chat_session_id=sid,
            ))

        history_messages = captured["input_messages"][:-1]  # exclude the new prompt itself
        history_texts = [m.content for m in history_messages]
        self.assertTrue(
            any("not available in the current turn" in text for text in history_texts),
            f"expected the historical marker in replayed history, got: {history_texts}",
        )
        # And critically, no raw image content block reaches turn 2's model.
        for text in history_texts:
            self.assertIsInstance(text, str)


if __name__ == "__main__":
    unittest.main()
