"""
ADR-004 addendum: bounded retry for HallucinationValidator's audit step.

Real, confirmed via repeated live calls during the Issue 1/Issue 2 fix
pass investigation: the exact same candidate answer plus the exact same
evidence envelope, resubmitted to the unchanged audit prompt, sometimes
returned "supported" and sometimes "unsupported"/"contradicted" across
independent real API calls (temperature=0 does not guarantee determinism
on hosted multi-tenant LLM APIs) - measured ~20% single-call
false-rejection rate on genuinely correct, fully-evidenced image
description answers, most visibly on single-turn "what's in this image?"
questions with no history at all.

Fixed by retrying ONLY the audit judgment call itself (never a new
candidate answer, never a relaxed evidence requirement, never touching
_validate_envelope's deterministic pre-checks) up to MAX_AUDIT_ATTEMPTS
(originally 3: 1 initial + 2 retries; revised down to 2: 1 initial + 1
retry after live latency testing showed the 3-attempt version's own
worst-case audit latency, ~45-55s, was a real contributor to requests
running long - see the ADR-004 addendum's "Why 2 total attempts" section)
total attempts, stopping at the first passing verdict. If every attempt
rejects, the turn still fails closed exactly as before - no change to
that guarantee. audit_attempts/audit_retry_used are attached to the
result's metadata either way and persisted through to the Neo4j-backed,
queryable audit trail (AgentAuditService.log_guard_check), not just left
in transient logs.

Also covers AUDIT_ATTEMPT_TIMEOUT_SECONDS, added alongside the revision:
a genuine per-attempt timeout so a single slow/hung audit call can no
longer let the retry loop's own worst case creep back up toward the outer
OUTPUT_GUARD_TIMEOUT_SECONDS - a timed-out attempt is an infrastructure
failure (same class as a raised exception), not a verdict flip-flop, so
it is not itself retried by this mechanism.
"""

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.governance.base import GuardResult, GuardStatus, IGuardValidator
from backend.governance.output_guard import OutputGuard
from backend.governance.validators.hallucination import (
    AUDIT_ATTEMPT_TIMEOUT_SECONDS,
    MAX_AUDIT_ATTEMPTS,
    HallucinationValidator,
)

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.main as main
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

from backend.tests.test_chat_attachment_repository import FakeChatAttachmentGraph


REJECTED_RESPONSE = json.dumps({
    "decision": "unsupported", "reason_category": "unsupported_claim",
    "unsupported_material_claims": 1, "confidence": 0.5,
})
PASSED_RESPONSE = json.dumps({
    "decision": "supported", "reason_category": "supported",
    "unsupported_material_claims": 0, "confidence": 1.0,
})

EVIDENCE_CONTEXT = {
    "tenant_id": "tenant_a",
    "source_text": "The image shows a blue circle and an orange square.",
}


class _AwaitableResponse:
    """A single-use awaitable standing in for one call's async return,
    since each call in a sequence must resolve to a *different* content
    string (unlike a plain AsyncMock(return_value=...), which is fixed)."""
    def __init__(self, content):
        self._content = content

    def __await__(self):
        async def _inner():
            return SimpleNamespace(content=self._content)
        return _inner().__await__()


def _manager_with_responses(*contents):
    raw_model = MagicMock()
    raw_model.invoke.side_effect = [SimpleNamespace(content=c) for c in contents]
    raw_model.ainvoke = MagicMock(side_effect=[_AwaitableResponse(c) for c in contents])
    manager = MagicMock()
    manager.get_raw_model_by_name.return_value = raw_model
    return manager, raw_model


class RetryLatencyUxRevisionTests(unittest.TestCase):
    """Locks in the two concrete, measured numbers from the retry-latency
    UX pass, so a future change to either constant is a deliberate,
    visible decision rather than a silent drift."""

    def test_max_audit_attempts_is_two_not_three(self):
        self.assertEqual(MAX_AUDIT_ATTEMPTS, 2)

    def test_audit_attempt_timeout_leaves_real_margin_under_the_outer_bound(self):
        # 2 attempts x AUDIT_ATTEMPT_TIMEOUT_SECONDS must stay meaningfully
        # under OUTPUT_GUARD_TIMEOUT_SECONDS's 60s default, leaving real
        # room for the rest of the validator chain (LlamaGuard,
        # DomainCompliance, PII redaction).
        worst_case_audit_latency = MAX_AUDIT_ATTEMPTS * AUDIT_ATTEMPT_TIMEOUT_SECONDS
        self.assertLess(worst_case_audit_latency, 60.0)
        self.assertGreaterEqual(60.0 - worst_case_audit_latency, 5.0)


class SyncAuditRetryTests(unittest.TestCase):
    def test_immediate_pass_uses_exactly_one_attempt(self):
        validator = HallucinationValidator()
        manager, raw_model = _manager_with_responses(PASSED_RESPONSE)
        validator._llm_mgr = manager

        result = validator.validate("A grounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.PASSED)
        self.assertEqual(result.metadata["audit_attempts"], 1)
        self.assertFalse(result.metadata["audit_retry_used"])
        self.assertEqual(raw_model.invoke.call_count, 1)

    def test_reject_then_pass_retries_once_and_returns_passed(self):
        validator = HallucinationValidator()
        manager, raw_model = _manager_with_responses(REJECTED_RESPONSE, PASSED_RESPONSE)
        validator._llm_mgr = manager

        result = validator.validate("A grounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.PASSED)
        self.assertEqual(result.metadata["audit_attempts"], 2)
        self.assertTrue(result.metadata["audit_retry_used"])
        self.assertEqual(raw_model.invoke.call_count, 2)
        # Same prompt both times - same candidate, same evidence, no
        # regeneration of anything.
        first_prompt = raw_model.invoke.call_args_list[0].args[0]
        second_prompt = raw_model.invoke.call_args_list[1].args[0]
        self.assertEqual(first_prompt, second_prompt)

    def test_all_attempts_reject_still_fails_closed(self):
        validator = HallucinationValidator()
        manager, raw_model = _manager_with_responses(
            REJECTED_RESPONSE, REJECTED_RESPONSE, REJECTED_RESPONSE,
        )
        validator._llm_mgr = manager

        result = validator.validate("An ungrounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.REJECTED)
        self.assertFalse(result.is_safe)
        self.assertEqual(result.violation_type, "HALLUCINATION_DETECTED")
        self.assertEqual(result.metadata["audit_attempts"], MAX_AUDIT_ATTEMPTS)
        self.assertTrue(result.metadata["audit_retry_used"])

    def test_retry_bound_is_never_exceeded(self):
        """Even if every attempt rejects, the model is called exactly
        MAX_AUDIT_ATTEMPTS times - never more."""
        validator = HallucinationValidator()
        manager, raw_model = _manager_with_responses(*([REJECTED_RESPONSE] * MAX_AUDIT_ATTEMPTS))
        validator._llm_mgr = manager

        validator.validate("An ungrounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(raw_model.invoke.call_count, MAX_AUDIT_ATTEMPTS)

    def test_deterministic_envelope_rejection_is_never_retried(self):
        """_validate_envelope's pure-logic pre-checks (missing evidence,
        fabricated evidence ids, etc.) happen before the audit loop and must
        never trigger a retry - retrying those would mean re-asking a
        question that isn't the audit judgment at all."""
        validator = HallucinationValidator()
        manager = MagicMock()
        validator._llm_mgr = manager

        result = validator.validate("The contract requires payment.", {})

        self.assertEqual(result.violation_type, "NO_EVIDENCE_RETRIEVED")
        manager.get_raw_model_by_name.assert_not_called()

    def test_infrastructure_failure_is_not_retried_by_this_mechanism(self):
        """A raised exception (provider error, malformed response) is a
        different failure mode from a verdict flip-flop and must still fail
        closed on the first occurrence, exactly as before this change."""
        validator = HallucinationValidator()
        manager = MagicMock()
        raw_model = MagicMock()
        raw_model.invoke.side_effect = RuntimeError("provider unavailable")
        manager.get_raw_model_by_name.return_value = raw_model
        validator._llm_mgr = manager

        result = validator.validate("bounded answer", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.VALIDATION_FAILED)
        self.assertEqual(raw_model.invoke.call_count, 1)

    def test_a_single_attempt_that_exceeds_the_per_attempt_timeout_fails_closed(self):
        """A slow/hung attempt must not be allowed to let the retry loop's
        own worst case creep up - it's an infrastructure failure (same
        class as a raised exception, see the test above), not a verdict
        flip-flop, so it must NOT be retried by this mechanism either."""
        validator = HallucinationValidator()
        manager = MagicMock()
        raw_model = MagicMock()

        def _slow_invoke(prompt):
            time.sleep(0.3)
            return SimpleNamespace(content=PASSED_RESPONSE)

        raw_model.invoke.side_effect = _slow_invoke
        manager.get_raw_model_by_name.return_value = raw_model
        validator._llm_mgr = manager

        with patch("backend.governance.validators.hallucination.AUDIT_ATTEMPT_TIMEOUT_SECONDS", 0.05):
            result = validator.validate("A grounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.VALIDATION_FAILED)
        self.assertEqual(result.violation_type, "VALIDATOR_INFRASTRUCTURE_FAILURE")
        self.assertEqual(raw_model.invoke.call_count, 1)


class AsyncAuditRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reject_then_pass_retries_once_and_returns_passed(self):
        validator = HallucinationValidator()
        manager, raw_model = _manager_with_responses(REJECTED_RESPONSE, PASSED_RESPONSE)
        validator._llm_mgr = manager

        result = await validator.avalidate("A grounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.PASSED)
        self.assertEqual(result.metadata["audit_attempts"], 2)
        self.assertTrue(result.metadata["audit_retry_used"])
        self.assertEqual(raw_model.ainvoke.call_count, 2)

    async def test_all_attempts_reject_still_fails_closed(self):
        validator = HallucinationValidator()
        manager, raw_model = _manager_with_responses(*([REJECTED_RESPONSE] * MAX_AUDIT_ATTEMPTS))
        validator._llm_mgr = manager

        result = await validator.avalidate("An ungrounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.REJECTED)
        self.assertEqual(result.metadata["audit_attempts"], MAX_AUDIT_ATTEMPTS)
        self.assertEqual(raw_model.ainvoke.call_count, MAX_AUDIT_ATTEMPTS)

    async def test_a_single_attempt_that_exceeds_the_per_attempt_timeout_fails_closed(self):
        """The production path (avalidate) is what OUTPUT_GUARD_TIMEOUT_
        SECONDS actually wraps end to end - this is the test that most
        directly proves a single hung audit call can no longer let the
        retry loop's own worst case approach that outer bound."""
        validator = HallucinationValidator()
        manager = MagicMock()
        raw_model = MagicMock()

        async def _slow_ainvoke(prompt):
            await asyncio.sleep(0.3)
            return SimpleNamespace(content=PASSED_RESPONSE)

        raw_model.ainvoke = _slow_ainvoke
        manager.get_raw_model_by_name.return_value = raw_model
        validator._llm_mgr = manager

        with patch("backend.governance.validators.hallucination.AUDIT_ATTEMPT_TIMEOUT_SECONDS", 0.05):
            result = await validator.avalidate("A grounded answer.", EVIDENCE_CONTEXT)

        self.assertEqual(result.status, GuardStatus.VALIDATION_FAILED)
        self.assertEqual(result.violation_type, "VALIDATOR_INFRASTRUCTURE_FAILURE")


class _StaticValidator(IGuardValidator):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def validate(self, input_text, context=None):
        return self.result


class AggregationSurfacesRetryTelemetryTests(unittest.TestCase):
    """OutputGuard's real chain runs LlamaGuardValidator -> DomainCompliance
    -> HallucinationValidator; all-PASSED is the common case, and base.py's
    tie-break (max() with equal priority) picks the FIRST validator's
    metadata as the aggregate base - never HallucinationValidator. This
    proves audit_attempts/audit_retry_used still reach the final,
    externally-visible result regardless of that tie-break."""

    def test_retry_telemetry_survives_when_a_different_validator_is_chosen(self):
        llama_guard_pass = _StaticValidator(GuardResult(is_safe=True))
        hallucination_pass_after_retry = _StaticValidator(GuardResult(
            is_safe=True,
            metadata={"audit_attempts": 2, "audit_retry_used": True},
        ))
        guard = OutputGuard(validators=[llama_guard_pass, hallucination_pass_after_retry])

        result = guard.validate("bounded answer")

        self.assertTrue(result.is_safe)
        self.assertEqual(result.metadata["audit_attempts"], 2)
        self.assertTrue(result.metadata["audit_retry_used"])

    def test_no_retry_telemetry_when_hallucination_validator_never_ran(self):
        llama_guard_reject = _StaticValidator(GuardResult(
            is_safe=False, status=GuardStatus.REJECTED, violation_type="UNSAFE_OUTPUT",
        ))
        guard = OutputGuard(validators=[llama_guard_reject])

        result = guard.validate("bounded answer")

        self.assertNotIn("audit_attempts", result.metadata)


class AuditTrailPersistenceTests(unittest.TestCase):
    """AgentAuditService.log_guard_check is what actually persists to the
    queryable Neo4j-backed audit trail (audit_logger.py's AuditLog nodes) -
    proves the retry telemetry reaches that persisted metadata, not just a
    transient log line."""

    def test_output_guard_log_carries_audit_attempts_and_retry_flag(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.infrastructure.agent_audit_service import AgentAuditService

        audit_logger = MagicMock()
        service = AgentAuditService(audit_logger=audit_logger, tenant_id="tenant_a")

        service.log_guard_check(
            guard_name="Output Guard", is_safe=True, violation_type=None,
            session_id="SESSION_1", validation_status="passed",
            audit_attempts=2, audit_retry_used=True,
        )

        metadata = audit_logger.log_event.call_args.kwargs["metadata"]
        self.assertEqual(metadata["audit_attempts"], 2)
        self.assertTrue(metadata["audit_retry_used"])

    def test_prompt_guard_log_leaves_audit_fields_none(self):
        """Scoping guard: Prompt Guard's own log_guard_check call site
        (main.py) never passes these fields - confirms the retry mechanism
        and its telemetry are Output Guard's audit step only."""
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.infrastructure.agent_audit_service import AgentAuditService

        audit_logger = MagicMock()
        service = AgentAuditService(audit_logger=audit_logger, tenant_id="tenant_a")

        service.log_guard_check(
            guard_name="Prompt Guard", is_safe=True, violation_type=None,
            session_id="SESSION_1", validation_status="passed",
        )

        metadata = audit_logger.log_event.call_args.kwargs["metadata"]
        self.assertIsNone(metadata["audit_attempts"])
        self.assertIsNone(metadata["audit_retry_used"])


class RunnerWiresAuditTelemetryToTheAuditTrailTests(unittest.IsolatedAsyncioTestCase):
    """End to end through the real runner() (matching
    test_chat_no_duplicate_ai_content_on_forced_retry.py's pattern) - proves
    main.py's actual Output Guard log_guard_check call site (not just the
    isolated pieces above) really passes audit_attempts/audit_retry_used
    through to AgentAuditService, and that Prompt Guard's own call site is
    unaffected."""

    async def _collect(self, agen):
        events = []
        async for item in agen:
            events.append(item)
        return events

    async def test_output_guard_log_call_carries_audit_retry_telemetry(self):
        graph = FakeChatAttachmentGraph()
        repo = Neo4jChatSessionRepository()
        repo.graph = graph
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]

        async def capturing_astream(*args, **kwargs):
            from langchain_core.messages import AIMessage, AIMessageChunk
            final_chunk = AIMessageChunk(content="A grounded answer.")
            yield ("messages", (final_chunk, {}))
            yield ("updates", {"assistant": {"messages": [AIMessage(content="A grounded answer.", tool_calls=[])]}})

        fake_model = MagicMock()
        fake_model.astream = capturing_astream
        fake_llm_mgr = MagicMock()
        fake_llm_mgr.get_model_by_name.return_value = fake_model

        with patch.object(main, "Neo4jChatSessionRepository", return_value=repo), \
             patch("backend.main.PromptGuard") as MockPromptGuard, \
             patch("backend.main.OutputGuard") as MockOutputGuard, \
             patch("backend.main.AuditLogger"), \
             patch("backend.infrastructure.agent_audit_service.AgentAuditService") as MockAgentAudit:
            MockPromptGuard.return_value.validate.return_value = MagicMock(is_safe=True, violation_type=None, message=None)
            MockOutputGuard.return_value.validate.return_value = MagicMock(
                is_safe=True, violation_type=None,
                metadata={"audit_attempts": 2, "audit_retry_used": True},
            )

            await self._collect(main.runner(
                model="gemini-2.5-flash", prompt="What's in this image?", history="[]",
                llm_mgr=fake_llm_mgr, tenant_id="tenant_a", chat_session_id=sid,
            ))

        calls = MockAgentAudit.return_value.log_guard_check.call_args_list
        output_guard_calls = [c for c in calls if c.kwargs.get("guard_name") == "Output Guard"]
        prompt_guard_calls = [c for c in calls if c.kwargs.get("guard_name") == "Prompt Guard"]

        self.assertEqual(len(output_guard_calls), 1)
        self.assertEqual(output_guard_calls[0].kwargs["audit_attempts"], 2)
        self.assertTrue(output_guard_calls[0].kwargs["audit_retry_used"])

        # Prompt Guard's own call site is untouched by this change.
        self.assertEqual(len(prompt_guard_calls), 1)
        self.assertNotIn("audit_attempts", prompt_guard_calls[0].kwargs)


if __name__ == "__main__":
    unittest.main()
