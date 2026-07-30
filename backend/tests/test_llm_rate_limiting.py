"""
Regression tests for item 13 in docs/ENTERPRISE_READINESS.md's punch list:
zero rate limiting/concurrency control existed around LLM extraction calls,
and StepExecutor's retry loop would blindly retry a rate-limit (429) error
up to max_retries times, each attempt re-sending the full contract text/
clause set - silently tripling cost on a single transient failure with no
realistic chance of success (especially for a per-day quota wall, which
cannot be waited out within one request's lifetime).

Reuses the same asyncio.Semaphore *concept* already established elsewhere
in this codebase (optimized_cuad_tools.py, embedding_optimizer.py) - here as
a shared threading.Semaphore (backend/shared/utils/llm_concurrency.py)
since LLMExtractionService/PolicyEvaluationService are synchronous methods,
not async def.
"""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.llm_extraction_service import LLMExtractionService, _LLMExtractionResponse, get_default_llm
    from backend.agents.policy_evaluation_service import PolicyEvaluationService, _LLMPolicyEvaluationResponse
    from backend.domain.policies.entities import PolicyRule
    from backend.agents.planning.execution_engine import StepExecutor, ExecutionResult, _is_quota_exhausted
    from backend.agents.planning.planning_agent import ExecutionStep, StepType

# This file tests concurrency/retry behavior, not caching - disable the P3-
# item-20 content-hash cache so repeated identical inputs across tests
# always exercise a real (fake) LLM call rather than returning a stale
# cached result.
_cache_disabled_patcher = patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False)
_cache_disabled_patcher.start()


def _wrap_raw(response):
    return {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1}),
        "parsed": response,
        "parsing_error": None,
    }


class FakeLLM:
    def __init__(self, response):
        self._response = response

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, prompt):
        return _wrap_raw(self._response)


class ConcurrencySemaphoreAppliedTests(unittest.TestCase):
    """Confirms the shared llm_call_semaphore actually wraps the outbound
    .invoke() call in both services - patched with a MagicMock so the
    context-manager protocol (__enter__/__exit__) can be asserted on
    directly, rather than needing to prove real concurrency limiting via
    timing (which would be flaky in a test)."""

    def test_extraction_service_acquires_and_releases_semaphore(self):
        fake_semaphore = MagicMock()
        with patch("backend.agents.llm_extraction_service.llm_call_semaphore", fake_semaphore):
            service = LLMExtractionService(FakeLLM(_LLMExtractionResponse(clauses=[])))
            service.extract_clauses("Some contract text.")

        fake_semaphore.__enter__.assert_called_once()
        fake_semaphore.__exit__.assert_called_once()

    def test_policy_evaluation_service_acquires_and_releases_semaphore(self):
        rule = PolicyRule(id="r1", rule_text="text", rule_type="mandatory",
                           applies_to=["general"], severity="HIGH", section_reference="s1")
        fake_semaphore = MagicMock()
        with patch("backend.agents.policy_evaluation_service.llm_call_semaphore", fake_semaphore):
            service = PolicyEvaluationService(FakeLLM(_LLMPolicyEvaluationResponse(violations=[])))
            service.evaluate_clause("Non-Compete", "Some clause text.", [rule])

        fake_semaphore.__enter__.assert_called_once()
        fake_semaphore.__exit__.assert_called_once()


class RealSemaphoreLimitsConcurrencyTests(unittest.TestCase):
    """End-to-end proof (real threading.Semaphore, real threads, no mocks on
    the semaphore itself) that a limit of 1 actually serializes overlapping
    calls rather than just being present but inert."""

    def test_semaphore_of_one_serializes_two_concurrent_extractions(self):
        from backend.shared.utils.llm_concurrency import llm_call_semaphore

        overlap_detected = threading.Event()
        currently_inside = {"count": 0}
        lock = threading.Lock()

        class SlowFakeLLM:
            def with_structured_output(self, schema, include_raw=True):
                return self

            def invoke(self, prompt):
                with lock:
                    currently_inside["count"] += 1
                    if currently_inside["count"] > 1:
                        overlap_detected.set()
                time.sleep(0.2)
                with lock:
                    currently_inside["count"] -= 1
                return _wrap_raw(_LLMExtractionResponse(clauses=[]))

        # Temporarily tighten the shared semaphore to 1 for this test only.
        original_value = llm_call_semaphore._value
        test_semaphore = threading.Semaphore(1)
        with patch("backend.agents.llm_extraction_service.llm_call_semaphore", test_semaphore):
            service = LLMExtractionService(SlowFakeLLM())
            threads = [
                threading.Thread(target=service.extract_clauses, args=("text",))
                for _ in range(3)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        self.assertFalse(overlap_detected.is_set(), "Semaphore of 1 should have serialized all calls - none should overlap")


class RateLimitAwareRetryTests(unittest.TestCase):
    """StepExecutor must not blindly retry a rate-limit (429) error the way
    it retries ordinary transient failures."""

    def _make_step(self, step_type):
        return ExecutionStep(step_id="s1", step_type=step_type, description="test step")

    def test_resource_exhausted_fails_immediately_without_retry(self):
        from google.api_core.exceptions import ResourceExhausted

        executor = StepExecutor()
        call_count = {"n": 0}

        async def fake_execute_clause_extraction(step, context):
            call_count["n"] += 1
            raise ResourceExhausted("quota exceeded")

        executor._execute_clause_extraction = fake_execute_clause_extraction

        import asyncio
        result = asyncio.run(executor._execute_step_with_retry(self._make_step(StepType.EXTRACT_CLAUSES), {}))

        self.assertFalse(result.success)
        self.assertEqual(call_count["n"], 1, "Should attempt exactly once - no retry into a rate limit")
        self.assertIn("Rate limit", result.error_message)

    def test_ordinary_exception_still_retries_as_before(self):
        executor = StepExecutor()
        call_count = {"n": 0}

        async def flaky_then_succeeds(step, context):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("transient network blip")
            return []

        executor._execute_clause_extraction = flaky_then_succeeds

        import asyncio
        result = asyncio.run(executor._execute_step_with_retry(self._make_step(StepType.EXTRACT_CLAUSES), {}))

        self.assertTrue(result.success)
        self.assertEqual(call_count["n"], 2, "Ordinary (non-rate-limit) failures should still retry as before")


class SDKLevelRetryCapTests(unittest.TestCase):
    """
    Regression test for a live end-to-end testing finding: a real quota-
    exhaustion error kept a worker retrying for 30+ seconds past a 3-minute
    client timeout, with backoff continuing to grow indefinitely.
    Root cause: langchain_google_genai.ChatGoogleGenerativeAI's own
    `max_retries` field defaults to 6 (mapped straight to
    google.genai.types.HttpRetryOptions(attempts=6)), retrying every single
    LLM call - including each per-clause policy-evaluation call - with
    growing exponential backoff before ever raising, independent of (and
    happening well before) execution_engine.py's StepExecutor-level fail-
    fast handling ever gets a chance to run.
    """

    def test_get_default_llm_caps_sdk_level_retries(self):
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key-not-real"}):
            llm = get_default_llm()

        self.assertIsNotNone(llm)
        # HttpRetryOptions: "If 0 or 1, it means no retries." Confirmed via
        # langchain_google_genai.chat_models: max_retries maps directly to
        # HttpRetryOptions(attempts=max_retries).
        self.assertEqual(llm.max_retries, 1)


class QuotaExhaustedDetectionTests(unittest.TestCase):
    """
    The real Gemini client (langchain_google_genai) wraps a real 429 into
    its own ChatGoogleGenerativeAIError (see chat_models._handle_client_
    error), NOT google.api_core.exceptions.ResourceExhausted - so
    StepExecutor's `except ResourceExhausted` fail-fast branch never
    actually matched a real quota-exhaustion error from this app's actual
    client, despite test_resource_exhausted_fails_immediately_without_retry
    (above) passing, since that test injects ResourceExhausted directly
    rather than the real exception type.
    """

    def test_detects_real_resource_exhausted_instance(self):
        from google.api_core.exceptions import ResourceExhausted
        self.assertTrue(_is_quota_exhausted(ResourceExhausted("quota exceeded")))

    def test_detects_message_based_quota_error_not_a_resource_exhausted_instance(self):
        # Simulates langchain_google_genai's real ChatGoogleGenerativeAIError,
        # which is a plain Exception subclass, not ResourceExhausted.
        fake_sdk_error = RuntimeError(
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
            "'message': 'You exceeded your current quota...'}}"
        )
        self.assertTrue(_is_quota_exhausted(fake_sdk_error))

    def test_ordinary_error_not_misdetected_as_quota_exhausted(self):
        self.assertFalse(_is_quota_exhausted(RuntimeError("transient network blip")))

    def test_step_executor_fails_fast_on_real_sdk_exception_type(self):
        """End-to-end proof at the StepExecutor layer: a
        ChatGoogleGenerativeAIError-shaped failure (message-based, not
        isinstance-based) must still fail fast without retrying, matching
        test_resource_exhausted_fails_immediately_without_retry's assertions
        but for the exception type actually raised in production."""
        executor = StepExecutor()
        call_count = {"n": 0}

        async def fake_execute_clause_extraction(step, context):
            call_count["n"] += 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded for generate_content_free_tier_requests")

        executor._execute_clause_extraction = fake_execute_clause_extraction

        import asyncio
        result = asyncio.run(executor._execute_step_with_retry(
            ExecutionStep(step_id="s1", step_type=StepType.EXTRACT_CLAUSES, description="test step"), {}
        ))

        self.assertFalse(result.success)
        self.assertEqual(call_count["n"], 1, "Should attempt exactly once - no retry into a rate limit")
        self.assertIn("Rate limit", result.error_message)


if __name__ == "__main__":
    unittest.main()
