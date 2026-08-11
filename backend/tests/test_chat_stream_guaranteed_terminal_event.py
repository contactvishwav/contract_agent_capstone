"""
Real, confirmed bug found live via process introspection (py-spy attached
to the running backend worker): a request hung for 400+ seconds with zero
further server-side activity, and zero explicit /api/chat/runs/.../cancel
call was ever made (confirmed via full log search) - yet
resilient_runner's own CancelledError handler had already fired
("Audit logged: processing_error - ... - cancelled"), meaning something
raised asyncio.CancelledError mid-generation from an unidentified source
(most likely an ASGI/Starlette client-disconnect check, possibly a false
positive - see ADR-004's addendum for what could and couldn't be confirmed
about the trigger itself).

resilient_runner's CancelledError handler persists server-side state and
re-raises with NO client-facing SSE event at all. cancellable_chat_stream's
cancellation-race loop only catches StopAsyncIteration around
next_event.result(), so any other exception - including this one -
propagated out uncaught, past both layers, leaving the browser's SSE
connection with nothing further ever arriving.

Fixed with _guaranteed_terminal_stream (backend/main.py), wrapped around
the ENTIRE stream in run() - the single outermost point - so regardless of
which inner layer fails, or why, the client always receives one
well-formed terminal 'end' event before the stream closes. This covers the
whole class of "some exception, from any cause, escapes every inner
handler," not just this one incident's specific trigger.

GENERATION_STALL_TIMEOUT_SECONDS itself was independently verified
correct in isolation (test_chat_generation_timeout.py, unchanged, still
passing) - the incident's stall-timeout "not firing" was because the
spurious cancellation interrupted the wait at ~3 seconds in, nowhere near
the 60s window, not because the timeout mechanism itself is broken. This
file additionally closes a real coverage gap: the existing stall-timeout
test only drove resilient_runner directly: it never proved the timeout
event survives cancellable_chat_stream, the actual production path (every
real request carries run_id + session_id - see input.tsx).
"""

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import (
        ChatRunRegistry,
        _guaranteed_terminal_stream,
        cancellable_chat_stream,
    )


class GuaranteedTerminalStreamUnitTests(unittest.IsolatedAsyncioTestCase):
    """Direct proof of the wrapper's own contract, before involving the
    real production call chain."""

    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    def _payloads(self, events):
        return [json.loads(e.removeprefix("data: ").strip()) for e in events]

    async def test_a_normal_stream_with_its_own_end_event_passes_through_unchanged(self):
        async def normal_stream():
            yield 'data: {"content": "hi", "type": "ai_message", "status": "passed"}\n\n'
            yield 'data: {"content": "", "type": "end", "status": "passed"}\n\n'

        events = await self._collect(_guaranteed_terminal_stream(normal_stream()))

        self.assertEqual(len(events), 2, "no extra fallback event may be appended to an already-terminal stream")
        payloads = self._payloads(events)
        self.assertEqual(payloads[-1]["type"], "end")
        self.assertEqual(payloads[-1]["status"], "passed")

    async def test_a_spurious_cancellederror_with_no_prior_terminal_event_still_yields_one(self):
        """The exact incident: a CancelledError raised mid-stream, with no
        terminal event yielded first (matching resilient_runner's own
        current, unchanged behavior) - the client must still get a real
        'end' event, not silence."""
        async def spuriously_cancelled_stream():
            yield 'data: {"content": "{\\"name\\": \\"EnhancedContractSearch\\"}", "type": "tool_call"}\n\n'
            raise asyncio.CancelledError()
            yield  # pragma: no cover

        events = await self._collect(_guaranteed_terminal_stream(spuriously_cancelled_stream()))

        payloads = self._payloads(events)
        self.assertEqual(len(payloads), 3)  # the tool_call echo + fallback error + fallback end
        self.assertEqual(payloads[-2]["type"], "error")
        self.assertEqual(payloads[-2]["status"], "cancelled")
        self.assertEqual(payloads[-1]["type"], "end")
        self.assertEqual(payloads[-1]["status"], "cancelled")

    async def test_cancellederror_after_a_real_terminal_event_adds_nothing_extra(self):
        """Never override a already-delivered real answer with a fallback
        'cancelled' event - matches cancellable_chat_stream's own existing
        'never release a later assistant answer' invariant."""
        async def cancelled_after_real_answer():
            yield 'data: {"content": "Payment is due within 90 days.", "type": "ai_message", "status": "passed"}\n\n'
            yield 'data: {"content": "", "type": "end", "status": "passed"}\n\n'
            raise asyncio.CancelledError()
            yield  # pragma: no cover

        events = await self._collect(_guaranteed_terminal_stream(cancelled_after_real_answer()))

        self.assertEqual(len(events), 2)
        payloads = self._payloads(events)
        self.assertEqual(payloads[-1]["status"], "passed")

    async def test_an_unexpected_generic_exception_still_yields_a_terminal_event(self):
        """Defense-in-depth beyond CancelledError specifically - any future
        uncaught exception class must not hang the client either."""
        async def broken_stream():
            yield 'data: {"content": "hi", "type": "user_message"}\n\n'
            raise RuntimeError("something nobody anticipated")
            yield  # pragma: no cover

        events = await self._collect(_guaranteed_terminal_stream(broken_stream()))

        payloads = self._payloads(events)
        self.assertEqual(payloads[-1]["type"], "end")
        self.assertEqual(payloads[-1]["status"], "generation_failed")

    async def test_a_stream_that_ends_silently_without_any_terminal_event_gets_one_anyway(self):
        """The ultimate backstop: even a stream that just stops (no
        exception, no terminal event - a contract violation that shouldn't
        happen, but this is the safety net for it) still resolves the
        client instead of leaving it hanging."""
        async def silently_incomplete_stream():
            yield 'data: {"content": "partial", "type": "tool_message"}\n\n'

        events = await self._collect(_guaranteed_terminal_stream(silently_incomplete_stream()))

        payloads = self._payloads(events)
        self.assertEqual(payloads[-1]["type"], "end")
        self.assertEqual(payloads[-1]["status"], "generation_failed")


class GuaranteedTerminalStreamThroughRealPipelineTests(unittest.IsolatedAsyncioTestCase):
    """Through the ACTUAL production call chain (cancellable_chat_stream,
    the same wrapping run() uses for every real request - input.tsx always
    sends run_id + session_id) - not just the wrapper in isolation."""

    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    def _payloads(self, events):
        return [json.loads(e.removeprefix("data: ").strip()) for e in events]

    async def test_spurious_cancellederror_through_the_real_pipeline_reaches_the_client(self):
        """Reproduces the exact incident shape: CancelledError raised from
        inside resilient_runner's replacement with run.cancel_requested
        NEVER set - i.e. no explicit /cancel call, matching the confirmed
        log evidence for the real incident."""
        registry = ChatRunRegistry()
        run = await registry.register("run-a", "tenant-a", "SESSION_A")

        async def spuriously_cancelled_resilient_runner(cancellation_observer=None, **kwargs):
            yield 'data: {"type": "tool_message", "content": "bounded evidence"}\n\n'
            raise asyncio.CancelledError()
            yield  # pragma: no cover

        with patch("backend.main.chat_run_registry", registry), \
             patch("backend.main.resilient_runner", spuriously_cancelled_resilient_runner):
            events = await self._collect(_guaranteed_terminal_stream(
                cancellable_chat_stream(run, model="gemini-2.5-flash", tenant_id="tenant-a", chat_session_id="SESSION_A"),
            ))

        self.assertFalse(run.cancel_requested.is_set(), "this must reproduce WITHOUT an explicit cancel request")
        payloads = self._payloads(events)
        self.assertEqual(payloads[-1]["type"], "end", f"client must receive a terminal event, got: {payloads}")

    async def test_explicit_stop_generating_through_the_real_pipeline_also_reaches_the_client(self):
        """Real, confirmed gap found live (not just the spurious-cancellation
        incident): cancellable_chat_stream's OWN deliberate cancellation-race
        branch (next_event.cancel() then a plain `break`) ends its generator
        via ordinary StopAsyncIteration, with no terminal event ever yielded
        - the exact same client-hanging gap, but via the legitimate
        Stop Generating button, not a spurious trigger. Confirmed live
        (POST .../cancel against a real running request) before this test
        was written: the client received nothing until this fix. Mirrors
        test_chat_run_session_persistence.py's
        test_server_cancel_interrupts_buffered_run_before_late_answer setup
        (same slow-phase-then-cancel shape), extended to also assert a
        terminal event, which that pre-existing test never checked for."""
        registry = ChatRunRegistry()
        run = await registry.register("run-c", "tenant-a", "SESSION_C")
        entered_slow_phase = asyncio.Event()

        async def slow_resilient_runner(cancellation_observer=None, **kwargs):
            yield 'data: {"type": "tool_message", "content": "bounded evidence"}\n\n'
            try:
                entered_slow_phase.set()
                await asyncio.Event().wait()
                yield 'data: {"type": "ai_message", "status": "passed", "content": "late"}\n\n'  # pragma: no cover
            except asyncio.CancelledError:
                if cancellation_observer:
                    cancellation_observer(True)
                raise

        with patch("backend.main.chat_run_registry", registry), \
             patch("backend.main.resilient_runner", slow_resilient_runner):
            collection = asyncio.create_task(self._collect(_guaranteed_terminal_stream(
                cancellable_chat_stream(run, model="gemini-2.5-flash", tenant_id="tenant-a", chat_session_id="SESSION_C"),
            )))
            await entered_slow_phase.wait()
            outcome = await registry.request_cancel("run-c", "tenant-a", "SESSION_C", timeout_seconds=1)
            events = await collection

        self.assertEqual(outcome, "cancelled")
        payloads = self._payloads(events)
        self.assertNotIn("late", json.dumps(payloads), "a cancelled run must never release a later assistant answer")
        self.assertEqual(payloads[-1]["type"], "end", f"client must receive a terminal event, got: {payloads}")

    async def test_genuine_stall_through_the_real_pipeline_still_fires_the_timeout(self):
        """Closes the real coverage gap: the pre-existing stall-timeout
        test only drove resilient_runner directly. This proves the same
        guarantee survives cancellable_chat_stream, the actual path every
        real request takes."""
        registry = ChatRunRegistry()
        run = await registry.register("run-b", "tenant-a", "SESSION_B")

        from langchain_core.messages import AIMessageChunk

        async def fake_astream(*args, **kwargs):
            yield ("messages", (AIMessageChunk(content=""), {}))
            await asyncio.Event().wait()  # genuinely stalls - never produces another chunk

        fake_model = MagicMock()
        fake_model.astream = fake_astream
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.get_model_by_name.return_value = fake_model

        with patch("backend.main.chat_run_registry", registry), \
             patch("backend.main.GENERATION_STALL_TIMEOUT_SECONDS", 0.05), \
             patch("backend.main.PromptGuard") as MockGuard, \
             patch("backend.main.AuditLogger"), \
             patch("backend.infrastructure.agent_audit_service.AgentAuditService"):
            MockGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)

            events = await asyncio.wait_for(
                self._collect(_guaranteed_terminal_stream(cancellable_chat_stream(
                    run, model="gemini-2.5-flash", prompt="payment terms please", history="[]",
                    llm_mgr=fake_llm_mgr, tenant_id="tenant_a",
                ))),
                timeout=5,
            )

        payloads = self._payloads(events)
        self.assertTrue(
            any(p["type"] == "error" and p.get("status") == "generation_timeout" for p in payloads),
            f"expected a generation_timeout error event, got: {payloads}",
        )
        self.assertEqual(payloads[-1]["type"], "end")


if __name__ == "__main__":
    unittest.main()
