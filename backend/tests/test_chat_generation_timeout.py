"""
Regression tests for the reconciliation-audit finding: runner()'s astream
consumption loop (backend/main.py) had no timeout at all - a hung/stalled
provider stream could hold the request open indefinitely, unlike every
other provider-facing call in this engagement (PDF extraction, reranking,
Output Guard - see ADR-004).

Matches test_chat_content_normalization.py's RunnerStreamingIntegrationTests
pattern: a fake LLM whose .astream() is driven directly, run through the
real runner()/resilient_runner() functions end to end, not a unit in
isolation.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch


class RunnerStalledGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_a_hung_generation_stream_is_cut_off_not_left_running_forever(self):
        from backend.main import resilient_runner

        async def fake_astream(*args, **kwargs):
            # A real provider stream that starts, then never produces
            # another chunk - the exact "hung/runaway" shape this timeout
            # exists to bound. asyncio.Event().wait() blocks forever, same
            # as a genuinely stuck network read would.
            yield ("messages", (MagicMock(tool_calls=[], content=""), {}))
            await asyncio.Event().wait()

        fake_model = MagicMock()
        fake_model.astream = fake_astream
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.get_model_by_name.return_value = fake_model

        with patch("backend.main.GENERATION_STALL_TIMEOUT_SECONDS", 0.05), \
             patch("backend.main.PromptGuard") as MockGuard, \
             patch("backend.main.AuditLogger"), \
             patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
            MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)

            # The concrete before/after proof: this call must actually
            # return (proving the stall was cut off), and promptly - not
            # hang for the test's own default timeout.
            events = await asyncio.wait_for(
                self._collect(resilient_runner(
                    model="gemini-2.5-flash", prompt="payment terms please", history="[]",
                    llm_mgr=fake_llm_mgr, tenant_id="tenant_a",
                )),
                timeout=5,
            )

        self.assertTrue(
            any('"type": "error"' in e and '"status": "generation_timeout"' in e for e in events),
            f"expected a generation_timeout error event, got: {events}",
        )
        self.assertTrue(
            any('"type": "end"' in e for e in events),
            "the stream must still reach a real terminal 'end' event after the cutoff",
        )

    async def test_a_normal_non_stalled_generation_is_unaffected(self):
        """Regression guard: the stall timeout must not fire for an
        ordinary generation that keeps producing chunks well within the
        window."""
        from langchain_core.messages import AIMessageChunk
        from backend.main import runner

        final_chunk = AIMessageChunk(content="The payment terms are net 90.")

        async def fake_astream(*args, **kwargs):
            yield ("messages", (final_chunk, {}))
            yield ("updates", {"assistant": {"messages": [final_chunk]}})

        fake_model = MagicMock()
        fake_model.astream = fake_astream
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.get_model_by_name.return_value = fake_model

        with patch("backend.main.GENERATION_STALL_TIMEOUT_SECONDS", 0.05), \
             patch("backend.main.PromptGuard") as MockGuard, \
             patch("backend.main.OutputGuard") as MockOutputGuard, \
             patch("backend.main.AuditLogger"), \
             patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
            MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)
            MockOutputGuard.return_value.validate.return_value = MagicMock(
                is_safe=True, violation_type=None, metadata={}
            )

            events = await self._collect(runner(
                model="gemini-2.5-flash", prompt="payment terms please", history="[]",
                llm_mgr=fake_llm_mgr, tenant_id="tenant_a",
            ))

        self.assertTrue(
            any("net 90" in e for e in events),
            "a normal, promptly-streaming generation must not be affected by the stall timeout",
        )
        self.assertFalse(
            any("generation_timeout" in e for e in events),
            "no timeout should ever fire for a generation that never stalls",
        )


if __name__ == "__main__":
    unittest.main()
