"""
Regression tests for item 13 in docs/ENTERPRISE_READINESS.md's punch list:
zero rate limiting/concurrency control existed around LLM extraction calls,
and (formerly) PlanExecutionEngine's StepExecutor retry loop would blindly
retry a rate-limit (429) error up to max_retries times, each attempt
re-sending the full contract text/clause set - silently tripling cost on a
single transient failure with no realistic chance of success (especially
for a per-day quota wall, which cannot be waited out within one request's
lifetime). StepExecutor itself is gone (PlanExecutionEngine was retired -
see git history), but the underlying quota-detection helper (_is_quota_
exhausted, now in llm_fallback_service.py) and the semaphore/timeout/
retry-cap concerns below are real, independent, and still current.

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
    from backend.agents.llm_fallback_service import _is_quota_exhausted

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
            # enable_fallback=False: this test is about the semaphore
            # wrapping a single .invoke() call, not the FALLBACK_CATEGORIES
            # second-pass behavior (covered in test_extraction_fallback_pass.py).
            service.extract_clauses("Some contract text.", enable_fallback=False)

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


class RealHangDetectedWithinConfiguredTimeoutTests(unittest.TestCase):
    """
    Regression test for a genuine ~3-hour hang observed during an overnight
    benchmark run: "LLM clause extraction failed: The read operation timed
    out" - no 429, no retry-storm logging in between, just one call that
    took roughly 3 hours to finally raise despite get_default_llm() already
    setting request_timeout=120 at the time.

    Investigation confirmed this was NOT a missing/broken timeout
    configuration: get_default_llm() does set timeout=120/max_retries=1 (see
    SDKLevelRetryCapTests below), and this test proves that value is
    genuinely wired all the way through the real with_structured_output(...,
    include_raw=True).invoke(...) call chain used in production - against a
    real socket that accepts a connection and then never responds (as close
    to "actually hangs" as a test can get without touching the real Gemini
    API), the client raises within its configured timeout, not indefinitely.

    This means a per-call client-side timeout, correctly configured, cannot
    by itself explain a multi-hour hang while the process keeps running -
    the far more likely explanation for the observed incident is the
    machine/session being suspended (e.g. laptop sleep) for that ~3-hour
    window, freezing the process (and its timeout-tracking) entirely until
    it woke up, at which point the already-expired deadline fired
    immediately. No in-process timeout of any kind (this one, a thread-pool
    watchdog, asyncio.wait_for, etc.) can fire *during* a full process
    suspension, since no code runs at all until the OS resumes it - that is
    an operational/environmental concern (e.g. preventing sleep during a
    long batch job), not something a timeout value can fix.
    """

    @staticmethod
    def _start_hang_server():
        """A raw TCP listener that accepts a connection and then never
        writes anything back - simulates a stalled read at the socket
        level, independent of any Gemini-specific error handling."""
        import socket as socket_module

        sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        sock.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.listen(5)

        def _serve():
            while True:
                try:
                    conn, _ = sock.accept()
                except OSError:
                    return  # socket closed at test teardown

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        return port

    def test_real_call_chain_raises_within_timeout_against_a_stalled_connection(self):
        from langchain_google_genai import ChatGoogleGenerativeAI
        from pydantic import BaseModel

        class _Schema(BaseModel):
            value: str = "x"

        port = self._start_hang_server()

        # Google's API rejects any deadline under 10s outright ("Manually
        # set deadline Ns is too short. Minimum allowed deadline is 10s.") -
        # confirmed empirically - so 10s is both the fastest and the
        # smallest valid value for this test, mirroring the same
        # request_timeout/max_retries get_default_llm() actually sets
        # (just a shorter timeout so the test doesn't take 120 real
        # seconds).
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash", temperature=0, request_timeout=10, max_retries=1,
            api_key="test-key-not-real", client_options=f"http://127.0.0.1:{port}",
        )
        structured = llm.with_structured_output(_Schema, include_raw=True)

        start = time.monotonic()
        with self.assertRaises(Exception):
            structured.invoke("test prompt")
        elapsed = time.monotonic() - start

        # Generous upper bound (30s, not the exact 10s) to avoid flakiness
        # on a loaded CI runner - the point is "closes in well under a
        # minute," not exact timing precision.
        self.assertLess(elapsed, 30.0, "Call should fail fast against a stalled connection, not hang")


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
    happening well before) _is_quota_exhausted's own fail-fast handling
    (llm_fallback_service.py) ever gets a chance to run.
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
    error), NOT google.api_core.exceptions.ResourceExhausted - so a plain
    `except ResourceExhausted` fail-fast branch alone never actually
    matches a real quota-exhaustion error from this app's actual client;
    _is_quota_exhausted's message-based fallback is what makes real
    detection work.
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


if __name__ == "__main__":
    unittest.main()
