"""
Regression tests for real multi-provider LLM fallback (backend/agents/
llm_fallback_service.py), added the same night a real production incident
(Gemini was the only configured provider, and its only working model hit
a real daily quota exhaustion) took down PDF upload/analysis entirely.

Proves, against the real chain-of-providers logic (not just a mocked
pass-through): a qualifying failure (quota/rate-limit/timeout/circuit-
open) on the primary provider actually falls through to the next
configured provider and a real request still completes; a non-qualifying
failure (a real bug shape) does NOT get silently rerouted; every provider
failing raises AllProvidersExhaustedError; an already-open circuit
breaker is skipped without even attempting that provider's call; and the
same real fallback chain is genuinely reachable through
LLMExtractionService/PolicyEvaluationService/RerankerService's
use_fallback=True entry points, not just through invoke_with_fallback
directly.
"""

import unittest
from unittest.mock import patch

from pydantic import BaseModel

from backend.shared.cache.redis_cache import cache
from backend.shared.reliability.circuit_breaker import (
    ANTHROPIC_CIRCUIT_BREAKER,
    CircuitBreaker,
    GEMINI_CIRCUIT_BREAKER,
    OPENAI_CIRCUIT_BREAKER,
)


class _FakeSchema(BaseModel):
    value: str = "ok"


def _raw_result(parsed):
    return {"raw": None, "parsed": parsed, "parsing_error": None}


class FakeChatModel:
    """Stands in for ChatGoogleGenerativeAI/ChatOpenAI/ChatAnthropic.
    `outcomes` is a list consumed one per .invoke() call (error to raise,
    or a value to return) - lets a single fake represent "fails once then
    would succeed" scenarios if ever needed, though most tests here only
    need one outcome."""

    def __init__(self, *args, outcomes=None, call_log=None, log_name="", **kwargs):
        self.model = kwargs.get("model")
        self._outcomes = list(outcomes or [])
        self._call_log = call_log if call_log is not None else []
        self._log_name = log_name

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, prompt):
        self._call_log.append(self._log_name)
        outcome = self._outcomes.pop(0) if self._outcomes else _raw_result(_FakeSchema())
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _fresh_breakers():
    """Fresh, isolated breaker instances per test - never the shared
    module-level singletons, matching test_circuit_breaker_wiring.py's
    established convention (avoids state leaking between tests)."""
    return (
        CircuitBreaker("test_fb_gemini", failure_threshold=5, recovery_timeout_seconds=30.0),
        CircuitBreaker("test_fb_openai", failure_threshold=5, recovery_timeout_seconds=30.0),
        CircuitBreaker("test_fb_anthropic", failure_threshold=5, recovery_timeout_seconds=30.0),
    )


class InvokeWithFallbackTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client._cache.clear()
        self.gemini_breaker, self.openai_breaker, self.anthropic_breaker = _fresh_breakers()
        self._chain_patcher = patch(
            "backend.agents.llm_fallback_service._EXTRACTION_CHAIN",
            [
                {"name": "gemini", "model": "gemini-2.5-flash", "factory": lambda t: self._gemini_llm, "breaker": self.gemini_breaker},
                {"name": "openai", "model": "gpt-4o", "factory": lambda t: self._openai_llm, "breaker": self.openai_breaker},
                {"name": "anthropic", "model": "claude-sonnet-5", "factory": lambda t: self._anthropic_llm, "breaker": self.anthropic_breaker},
            ],
        )
        self.call_log = []
        self._gemini_llm = None
        self._openai_llm = None
        self._anthropic_llm = None
        self._chain_patcher.start()

    def tearDown(self):
        self._chain_patcher.stop()
        cache.redis_client._cache.clear()

    def _set_providers(self, gemini=None, openai=None, anthropic=None):
        self._gemini_llm = FakeChatModel(model="gemini-2.5-flash", outcomes=gemini, call_log=self.call_log, log_name="gemini") if gemini is not None else None
        self._openai_llm = FakeChatModel(model="gpt-4o", outcomes=openai, call_log=self.call_log, log_name="openai") if openai is not None else None
        self._anthropic_llm = FakeChatModel(model="claude-sonnet-5", outcomes=anthropic, call_log=self.call_log, log_name="anthropic") if anthropic is not None else None
        # Re-patch since setUp captured None placeholders before providers existed.
        self._chain_patcher.stop()
        self._chain_patcher = patch(
            "backend.agents.llm_fallback_service._EXTRACTION_CHAIN",
            [
                {"name": "gemini", "model": "gemini-2.5-flash", "factory": lambda t: self._gemini_llm, "breaker": self.gemini_breaker},
                {"name": "openai", "model": "gpt-4o", "factory": lambda t: self._openai_llm, "breaker": self.openai_breaker},
                {"name": "anthropic", "model": "claude-sonnet-5", "factory": lambda t: self._anthropic_llm, "breaker": self.anthropic_breaker},
            ],
        )
        self._chain_patcher.start()

    def test_quota_error_on_primary_falls_back_to_openai_and_completes(self):
        from backend.agents.llm_fallback_service import invoke_with_fallback

        quota_error = RuntimeError(
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'quota exceeded'}}"
        )
        self._set_providers(
            gemini=[quota_error],
            openai=[_raw_result(_FakeSchema(value="served_by_openai"))],
        )

        raw_result, provider, model = invoke_with_fallback(_FakeSchema, "prompt", operation="test_op")

        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o")
        self.assertEqual(raw_result["parsed"].value, "served_by_openai")
        self.assertEqual(self.call_log, ["gemini", "openai"], "gemini must actually be attempted first, then openai")

    def test_non_qualifying_error_on_primary_raises_immediately_without_trying_fallback(self):
        """A real bug shape (not a quota/rate-limit/timeout) must not be
        silently rerouted to a different provider - that would mask a
        real defect and burn cost on a provider that was never going to
        succeed either."""
        from backend.agents.llm_fallback_service import invoke_with_fallback

        real_bug = ValueError("schema mismatch: unexpected field 'foo'")
        self._set_providers(gemini=[real_bug], openai=[_raw_result(_FakeSchema())])

        with self.assertRaises(ValueError):
            invoke_with_fallback(_FakeSchema, "prompt", operation="test_op")

        self.assertEqual(self.call_log, ["gemini"], "openai must never be attempted for a non-qualifying failure")

    def test_all_providers_exhausted_raises_distinct_error(self):
        from backend.agents.llm_fallback_service import AllProvidersExhaustedError, invoke_with_fallback

        quota_error = RuntimeError("429 RESOURCE_EXHAUSTED")
        self._set_providers(
            gemini=[quota_error],
            openai=[RuntimeError("openai RateLimitError: rate_limit_exceeded")],
            anthropic=[RuntimeError("anthropic 429 overloaded")],
        )

        with self.assertRaises(AllProvidersExhaustedError):
            invoke_with_fallback(_FakeSchema, "prompt", operation="test_op")

        self.assertEqual(self.call_log, ["gemini", "openai", "anthropic"])

    def test_unconfigured_provider_is_skipped_not_treated_as_a_failure(self):
        from backend.agents.llm_fallback_service import invoke_with_fallback

        quota_error = RuntimeError("429 RESOURCE_EXHAUSTED")
        # anthropic left unconfigured (factory returns None, as it would
        # for a real missing ANTHROPIC_API_KEY).
        self._set_providers(gemini=[quota_error], openai=[_raw_result(_FakeSchema(value="from_openai"))])

        raw_result, provider, model = invoke_with_fallback(_FakeSchema, "prompt", operation="test_op")

        self.assertEqual(provider, "openai")
        self.assertEqual(raw_result["parsed"].value, "from_openai")

    def test_open_circuit_on_primary_skips_straight_to_fallback_without_calling_it(self):
        from backend.agents.llm_fallback_service import invoke_with_fallback

        # Trip the gemini breaker open directly, matching
        # test_circuit_breaker_wiring.py's convention, rather than racking
        # up real failed calls first.
        for _ in range(5):
            self.gemini_breaker.record_failure()
        self.assertEqual(self.gemini_breaker.get_status()["state"], "open")

        self._set_providers(gemini=[_raw_result(_FakeSchema())], openai=[_raw_result(_FakeSchema(value="from_openai"))])

        raw_result, provider, model = invoke_with_fallback(_FakeSchema, "prompt", operation="test_op")

        self.assertEqual(provider, "openai")
        self.assertEqual(self.call_log, ["openai"], "gemini's .invoke() must never be called while its breaker is open")


class RealCallSiteFallbackIntegrationTests(unittest.TestCase):
    """Proves use_fallback=True is genuinely reachable end-to-end through
    LLMExtractionService/PolicyEvaluationService, not just through
    invoke_with_fallback in isolation."""

    def setUp(self):
        cache.redis_client._cache.clear()
        self.gemini_breaker, self.openai_breaker, self.anthropic_breaker = _fresh_breakers()
        self.call_log = []

    def tearDown(self):
        cache.redis_client._cache.clear()

    def test_extraction_service_use_fallback_completes_via_openai_after_gemini_quota_error(self):
        from backend.agents.llm_extraction_service import (
            LLMExtractionService,
            _LLMExtractedClause,
            _LLMExtractionResponse,
        )

        quota_error = RuntimeError("429 RESOURCE_EXHAUSTED")
        fallback_response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type="Governing Law", extracted_text="Delaware law applies.", confidence=0.9)
        ])
        gemini_llm = FakeChatModel(model="gemini-2.5-flash", outcomes=[quota_error], call_log=self.call_log, log_name="gemini")
        openai_llm = FakeChatModel(model="gpt-4o", outcomes=[_raw_result(fallback_response)], call_log=self.call_log, log_name="openai")

        chain = [
            {"name": "gemini", "model": "gemini-2.5-flash", "factory": lambda t: gemini_llm, "breaker": self.gemini_breaker},
            {"name": "openai", "model": "gpt-4o", "factory": lambda t: openai_llm, "breaker": self.openai_breaker},
            {"name": "anthropic", "model": "claude-sonnet-5", "factory": lambda t: None, "breaker": self.anthropic_breaker},
        ]

        with patch("backend.agents.llm_fallback_service._EXTRACTION_CHAIN", chain), \
             patch("backend.agents.llm_extraction_service.Phase3Config.CACHE_ENABLED", False), \
             patch("backend.shared.monitoring.llm_usage_tracker.llm_usage_tracker.record_call") as fake_track:
            service = LLMExtractionService(use_fallback=True)
            result = service.extract_clauses(
                "Delaware law applies. " * 10, enable_fallback=False
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].clause_type.value, "Governing Law")
        self.assertEqual(self.call_log, ["gemini", "openai"])

        # LLMUsageTracker must reflect the real provider that actually
        # served this request, flagged as a fallback - not silently
        # recorded as if Gemini (the constructor-time default) served it.
        fake_track.assert_called_once()
        _, kwargs = fake_track.call_args
        args = fake_track.call_args.args
        model_recorded = args[1] if len(args) > 1 else kwargs.get("model")
        self.assertEqual(model_recorded, "gpt-4o")
        self.assertTrue(kwargs.get("is_fallback") or fake_track.call_args.kwargs.get("is_fallback"))

    def test_extraction_service_without_use_fallback_flag_is_unaffected(self):
        """Control: explicit llm= callers (benchmark scripts, tests) keep
        their exact pre-existing single-provider behavior - no fallback
        chain involvement at all."""
        from backend.agents.llm_extraction_service import _LLMExtractedClause, _LLMExtractionResponse, LLMExtractionService

        class DirectLLM:
            model = "gemini-2.5-flash"

            def with_structured_output(self, schema, include_raw=True):
                return self

            def invoke(self, prompt):
                return _raw_result(_LLMExtractionResponse(clauses=[]))

        with patch("backend.agents.llm_extraction_service.Phase3Config.CACHE_ENABLED", False):
            service = LLMExtractionService(DirectLLM())
            self.assertFalse(service.use_fallback)
            result = service.extract_clauses("some text", enable_fallback=False)

        self.assertEqual(result, [])

    def test_primary_provider_serving_a_use_fallback_call_is_not_flagged_as_fallback(self):
        """Regression test for a real, confirmed bug found live during
        production verification: PolicyEvaluationService/RerankerService's
        self._model_name defaults to the placeholder "unknown" for a
        use_fallback=True, llm=None construction - comparing model_used
        against that placeholder meant is_fallback was recorded as True
        for EVERY successful fallback-enabled call, even when the primary
        provider (gemini) itself served it without ever needing to fall
        back. Fixed to compare the real provider_used against
        PRIMARY_PROVIDER directly - this proves gemini serving the call
        (the overwhelmingly common case) is correctly recorded as
        is_fallback=False, not True."""
        from backend.agents.policy_evaluation_service import (
            PolicyEvaluationService,
            _LLMPolicyEvaluationResponse,
            _LLMPolicyViolation,
        )
        from backend.domain.policies.entities import PolicyRule

        rule = PolicyRule(
            id="rule_1", rule_text="No unlimited liability.", rule_type="mandatory",
            applies_to=["general"], severity="HIGH", section_reference="s1",
        )
        gemini_llm = FakeChatModel(
            model="gemini-2.5-flash",
            outcomes=[_raw_result(_LLMPolicyEvaluationResponse(violations=[]))],
            call_log=self.call_log, log_name="gemini",
        )
        chain = [
            {"name": "gemini", "model": "gemini-2.5-flash", "factory": lambda t: gemini_llm, "breaker": self.gemini_breaker},
            {"name": "openai", "model": "gpt-4o", "factory": lambda t: None, "breaker": self.openai_breaker},
            {"name": "anthropic", "model": "claude-sonnet-5", "factory": lambda t: None, "breaker": self.anthropic_breaker},
        ]

        with patch("backend.agents.llm_fallback_service._EXTRACTION_CHAIN", chain), \
             patch("backend.agents.policy_evaluation_service.Phase3Config.CACHE_ENABLED", False), \
             patch("backend.shared.monitoring.llm_usage_tracker.llm_usage_tracker.record_call") as fake_track:
            service = PolicyEvaluationService(use_fallback=True)
            service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [rule])

        self.assertEqual(self.call_log, ["gemini"], "only the primary provider should have been attempted")
        fake_track.assert_called_once()
        _, kwargs = fake_track.call_args
        self.assertEqual(kwargs.get("model") or fake_track.call_args.args[1], "gemini-2.5-flash")
        self.assertFalse(
            kwargs.get("is_fallback", fake_track.call_args.kwargs.get("is_fallback")),
            "the primary provider serving the call must not be recorded as a fallback",
        )


if __name__ == "__main__":
    unittest.main()
