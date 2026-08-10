"""
Reconciliation-audit item 3: does switching providers mid-conversation on a
SUCCESSFUL turn actually preserve context, not just handle a failed switch
honestly? Matches test_chat_run_session_persistence.py's pattern (real
runner(), fake LLM, shared FakeChatSessionGraph-backed repository) - the
earlier reconciliation report found this exact scenario had never been
tested: every existing test used one fixed model per session, and the one
relevant doc claim (pdf-citations-model-selection.md) only proves failure-
handling/attribution bookkeeping on a 404, not that a successful switch's
new provider actually receives prior turns as context.

Session history load (_messages_from_stored) is independent of which model
is requested for the new turn - runner() never locks a session_id to a
model. This test drives two real turns with one model, switches to a
different, real, distinctly-configured model for a third turn, and asserts
on the literal `input["messages"]` the new provider's .astream() received.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, AIMessageChunk

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
    from backend.main import runner

from backend.tests.test_chat_session_repository import FakeChatSessionGraph


def _make_shared_repo():
    graph = FakeChatSessionGraph()
    repo = Neo4jChatSessionRepository()
    repo.graph = graph
    return repo, graph


def _simple_turn_astream(answer_text: str):
    """A minimal single-turn astream: no tool calls, just a final answer -
    isolates this test to the context-preservation question, not tool-call
    plumbing (already covered elsewhere)."""
    final_chunk = AIMessageChunk(content=answer_text)
    final_assistant = AIMessage(content=answer_text, tool_calls=[])

    async def fake_astream(*args, **kwargs):
        yield ("messages", (final_chunk, {}))
        yield ("updates", {"assistant": {"messages": [final_assistant]}})

    return fake_astream


class MultiModelSwitchContextPreservationTests(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_switching_provider_mid_session_feeds_the_new_provider_prior_turns(self):
        session_repo, _ = _make_shared_repo()
        session = session_repo.create_session("tenant_a", None, "Cross-model termination question")
        sid = session["session_id"]

        gemini_mgr = MagicMock()
        gpt4o_captured = {}

        with patch("backend.main.Neo4jChatSessionRepository", return_value=session_repo), \
             patch("backend.main.PromptGuard") as MockGuard, \
             patch("backend.main.OutputGuard") as MockOutputGuard, \
             patch("backend.main.AuditLogger"), \
             patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
            MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)
            MockOutputGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, metadata={})

            # Turn 1, Gemini.
            model_1 = MagicMock()
            model_1.astream = _simple_turn_astream("The termination clause requires 30 days written notice.")
            gemini_mgr.get_model_by_name.return_value = model_1
            await self._collect(runner(
                model="gemini-2.5-flash", prompt="What does the termination clause say?", history="[]",
                llm_mgr=gemini_mgr, tenant_id="tenant_a", chat_session_id=sid,
            ))

            # Turn 2, still Gemini.
            model_2 = MagicMock()
            model_2.astream = _simple_turn_astream("Either party may invoke it, not just the client.")
            gemini_mgr.get_model_by_name.return_value = model_2
            await self._collect(runner(
                model="gemini-2.5-flash", prompt="Who is allowed to invoke that clause?", history="[]",
                llm_mgr=gemini_mgr, tenant_id="tenant_a", chat_session_id=sid,
            ))

            # Turn 3: switch to a different, real, configured provider
            # (gpt-4o). A fresh llm_mgr/model, as a real provider switch
            # would use - captures exactly what this new provider's
            # .astream() actually received as input.
            async def capturing_astream(*args, **kwargs):
                gpt4o_captured["input_messages"] = kwargs["input"]["messages"]
                final_chunk = AIMessageChunk(content="Yes, this applies to both parties equally.")
                final_assistant = AIMessage(content="Yes, this applies to both parties equally.", tool_calls=[])
                yield ("messages", (final_chunk, {}))
                yield ("updates", {"assistant": {"messages": [final_assistant]}})

            gpt4o_model = MagicMock()
            gpt4o_model.astream = capturing_astream
            gpt4o_mgr = MagicMock()
            gpt4o_mgr.get_model_by_name.return_value = gpt4o_model

            await self._collect(runner(
                model="gpt-4o", prompt="Does that apply symmetrically to both sides?", history="[]",
                llm_mgr=gpt4o_mgr, tenant_id="tenant_a", chat_session_id=sid,
            ))

        # The concrete proof: gpt-4o's real input included turns 1-2's real
        # content, not a fresh empty thread.
        self.assertIn("input_messages", gpt4o_captured, "gpt-4o's astream() was never even called")
        received_texts = [getattr(m, "content", "") for m in gpt4o_captured["input_messages"]]
        self.assertEqual(
            received_texts,
            [
                "What does the termination clause say?",
                "The termination clause requires 30 days written notice.",
                "Who is allowed to invoke that clause?",
                "Either party may invoke it, not just the client.",
                "Does that apply symmetrically to both sides?",
            ],
            "the new provider must receive the full prior conversation in order, not a fresh thread",
        )

        # And the switch is honestly attributed, not silently relabeled.
        messages = session_repo.list_messages(sid, "tenant_a")
        self.assertEqual([m["model"] for m in messages if m["role"] == "ai_message"], [
            "gemini-2.5-flash", "gemini-2.5-flash", "gpt-4o",
        ])


if __name__ == "__main__":
    unittest.main()
