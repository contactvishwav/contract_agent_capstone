"""
Contract Chat functional audit, item 4 (the blank-page crash), root-
caused: main.py's runner() forwarded chunk[0].content (LangChain's
AIMessageChunk.content) to the frontend completely unnormalized. Its
shape isn't consistent - usually a plain str, but for some responses
(confirmed live: any direct final-text turn with no preceding tool
call, at least with Gemini) it's a list of content-block dicts instead,
e.g. [{"type": "text", "text": "...", "extras": {"signature": "..."},
"index": 0}] - Gemini's thought-signature grounding metadata riding
along as a structured block.

The frontend's message.tsx renders unknown part types via
`<Fragment>{content}</Fragment>` with no shape check - when content was
that list of dicts, React threw "Objects are not valid as a React
child". ChatPage had no ErrorBoundary (also fixed - see the frontend
App.tsx change), so that crash took down the entire app: a blank white
page, confirmed live and reproduced during the audit.

Fixed by flattening any content-block-list shape to plain text before
it's ever yielded to the frontend.

A second, real instance of the exact same bug was found live during
final verification of this fix, not caught by the first pass: runner()
has a *second*, separate accumulation site for the same chunk[0].content
- `ai_full_content += chunk.content`, a buffer used for the post-
generation Output Guard/hallucination check - that wasn't covered by
the first pass (which only normalized the two `yield` sites). It threw
the identical "can only concatenate str (not list) to str" TypeError
whenever content was the list-shape, which crashed the whole streaming
response mid-generation with no 'end' event ever sent - reproduced
live: the real HTTP request completed in ~6 seconds with the real
answer text truncated mid-sentence and no error surfaced to the client
at all. RunnerStreamingIntegrationTests below exercises runner()'s
actual streaming loop (not just the pure helper function) specifically
to catch this class of "missed a second call site" bug in the future.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.main import _normalize_ai_message_content


class NormalizeAiMessageContentTests(unittest.TestCase):
    def test_plain_string_passes_through_unchanged(self):
        self.assertEqual(_normalize_ai_message_content("I couldn't find that."), "I couldn't find that.")

    def test_empty_string_passes_through(self):
        self.assertEqual(_normalize_ai_message_content(""), "")

    def test_content_block_list_is_flattened_to_text(self):
        """The exact real shape confirmed live."""
        content = [{
            "type": "text",
            "text": "I can help analyze a contract, but I need more information.",
            "extras": {"signature": "CmABEU0yD74g..."},
            "index": 0,
        }]
        result = _normalize_ai_message_content(content)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "I can help analyze a contract, but I need more information.")

    def test_multiple_text_blocks_are_concatenated(self):
        content = [
            {"type": "text", "text": "Part one. ", "index": 0},
            {"type": "text", "text": "Part two.", "index": 1},
        ]
        self.assertEqual(_normalize_ai_message_content(content), "Part one. Part two.")

    def test_blocks_without_text_are_skipped_not_crashed_on(self):
        content = [
            {"type": "thought_signature", "extras": {"signature": "abc"}},
            {"type": "text", "text": "The real answer.", "index": 1},
        ]
        self.assertEqual(_normalize_ai_message_content(content), "The real answer.")

    def test_none_content_becomes_empty_string_not_none(self):
        """A JSON-serialized None would still be a valid frontend value,
        but empty string matches every other empty-content case here and
        keeps message.tsx's rendering logic uniform."""
        self.assertEqual(_normalize_ai_message_content(None), "")

    def test_result_is_always_json_serializable_as_a_plain_value(self):
        """The real invariant this whole fix protects: whatever comes out
        of this function must never again be something React (or
        json.dumps) chokes on."""
        import json
        for content in ["plain", [{"type": "text", "text": "x"}], [], None, ""]:
            normalized = _normalize_ai_message_content(content)
            self.assertIsInstance(normalized, str)
            json.dumps({"content": normalized})  # must not raise


class RunnerStreamingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercises runner()'s real streaming loop end to end with a fake
    LLM whose .astream() yields the exact real, confirmed shape (a list-
    content AIMessageChunk with no preceding tool call) - proving the
    whole function survives it, not just the pure helper in isolation."""

    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_list_shaped_final_chunk_does_not_crash_the_stream(self):
        from langchain_core.messages import AIMessageChunk
        from backend.main import runner

        list_shaped_chunk = AIMessageChunk(content=[
            {"type": "text", "text": "The total project fee is $500,000.", "index": 0}
        ])

        async def fake_astream(*args, **kwargs):
            yield ("messages", (list_shaped_chunk, {}))
            yield ("updates", {"assistant": {"messages": [list_shaped_chunk]}})

        fake_model = MagicMock()
        fake_model.astream = fake_astream
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.get_model_by_name.return_value = fake_model

        with patch("backend.main.PromptGuard") as MockGuard, \
             patch("backend.main.OutputGuard") as MockOutputGuard, \
             patch("backend.main.AuditLogger"), \
             patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
            MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)
            MockOutputGuard.return_value.validate.return_value = MagicMock(
                is_safe=True, violation_type=None, metadata={}
            )

            # Real regression: pre-fix, this raised TypeError deep inside
            # the async generator, which surfaces here as the stream
            # simply never producing an 'end' event (exactly what a real
            # client saw: a connection that closed with a truncated
            # answer and no error).
            events = await self._collect(runner(
                model="gemini-2.5-flash", prompt="payment terms please", history="[]",
                llm_mgr=fake_llm_mgr, tenant_id="tenant_a",
            ))

        self.assertTrue(
            any('"type": "end"' in e for e in events),
            "the stream must reach a real 'end' event, not die mid-generation",
        )
        self.assertTrue(
            any("$500,000" in e for e in events),
            "the real answer text must actually reach the client",
        )


if __name__ == "__main__":
    unittest.main()
