"""
Real, confirmed bug found live while investigating a manually-reported
multi-turn image issue: main.py's runner() accumulated ai_full_content
(the buffered candidate answer sent to Output Guard) from raw
stream_mode="messages" AIMessageChunk tokens. contract_chat_agent.py's
assistant node can invoke the LLM more than once per turn - once producing
a real, non-empty answer with no tool_calls (discarded and overridden by
the forced-retrieval fallback when there's no current-turn evidence), then
again after the forced tool executes, producing the real final answer.
"messages" stream mode emits tokens from EVERY underlying LLM call inside
the node regardless of what the node's own logic does with the return
value, so ai_full_content ended up with the discarded first answer's text
followed immediately by the real final answer's text, concatenated with no
separator - reproduced live as the exact same sentence appearing twice in
a row. HallucinationValidator then correctly (from its own perspective,
given the corrupted candidate text) rejected the turn as an unsupported/
contradicted claim - directly causing a real, honest, single-sentence
decline to be withheld from the user.

Fixed by deriving ai_full_content from the "updates" stream's assistant
message content instead (the graph's own authoritative per-step return
value, already trusted for tool_calls extraction) - assigned, not
appended, on each assistant step, so only the LAST step's content (always
the true final answer, since the graph only stops once a response has no
further tool_calls) survives.
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.main as main
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph


class NoDuplicateAiContentOnForcedRetryTests(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_discarded_first_answer_is_not_concatenated_with_the_real_final_answer(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]

        from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

        real_final_answer = "I no longer have access to that image - please re-attach it."

        async def capturing_astream(*args, **kwargs):
            # Step 1: the model's real first answer, streamed as tokens,
            # but never actually used - the node discards it and forces a
            # tool call instead (matches contract_chat_agent.py's
            # assistant() when there's no current-turn evidence).
            discarded_first_answer = AIMessageChunk(content=real_final_answer)
            yield ("messages", (discarded_first_answer, {}))
            forced_tool_call = AIMessage(content="", tool_calls=[{
                "name": "EnhancedContractSearch", "args": {"search_level": "all"}, "id": "forced_1",
            }])
            yield ("updates", {"assistant": {"messages": [forced_tool_call]}})

            # Step 2: the tool "executes" (a trivial, empty result).
            tool_result = ToolMessage(content="{}", tool_call_id="forced_1")
            yield ("updates", {"tools": {"messages": [tool_result]}})

            # Step 3: the model's real final answer, streamed as tokens
            # again, and this time actually returned by the node.
            final_chunk = AIMessageChunk(content=real_final_answer)
            yield ("messages", (final_chunk, {}))
            final_assistant = AIMessage(content=real_final_answer, tool_calls=[])
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
            captured_candidate = {}

            def capture_and_pass(content, context_metadata=None):
                captured_candidate["content"] = content
                return MagicMock(is_safe=True, violation_type=None, metadata={})
            MockOutputGuard.return_value.validate.side_effect = capture_and_pass

            await self._collect(main.runner(
                model="gemini-2.5-flash", prompt="What's in the image you saw?", history="[]",
                llm_mgr=fake_llm_mgr, tenant_id="tenant_a", chat_session_id=sid,
            ))

        # The exact bug: this used to equal real_final_answer * 2 concatenated
        # with no separator.
        self.assertEqual(captured_candidate["content"], real_final_answer)

        persisted = repo.list_messages(sid, "tenant_a")
        final_ai_row = next(m for m in persisted if m["role"] == "ai_message")
        self.assertEqual(final_ai_row["content"], real_final_answer)


if __name__ == "__main__":
    unittest.main()
