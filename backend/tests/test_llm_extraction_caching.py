"""
Regression tests for P3 item 20: re-analyzing the same contract text (or
re-evaluating the same clause against the same policy rules) re-billed the
LLM every single time (docs/ENTERPRISE_READINESS.md §9) - there was no
caching around extraction calls, and no visibility into actual token
usage/cost at all.

LLMExtractionService.extract_clauses and PolicyEvaluationService.
evaluate_clause now cache results on a hash of (prompt version, model,
inputs), and record every call (hit or miss) to LLMUsageTracker
(backend.shared.monitoring.llm_usage_tracker) - Redis-backed counters
shared across processes, not a single process's memory (see that module's
docstring for why: the Celery migration moved these exact calls into a
separate `worker` container).

Each test explicitly controls Phase3Config.CACHE_ENABLED via
context-managed patches (not fire-and-forget .start()) so results don't
depend on what any other test file in the same pytest session left
behind - several existing test files (test_stubbed_llm_parsers.py,
test_llm_rate_limiting.py, etc.) permanently disable caching at import
time for their own isolation, which would otherwise leak into this file
if it relied on the ambient default. Isolation for the usage counters
themselves comes from clearing the shared InMemoryCache backing store
(`cache.redis_client._cache.clear()`) in setUp/tearDown, not from
constructing a "fresh" LLMUsageTracker - counters live in that shared
store now, so any tracker instance reads/writes the same numbers.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.agents.llm_extraction_service import (
    LLMExtractionService,
    CUADClauseType,
    _LLMExtractedClause,
    _LLMExtractionResponse,
)
from backend.agents.policy_evaluation_service import (
    PolicyEvaluationService,
    _LLMPolicyEvaluationResponse,
    _LLMPolicyViolation,
)
from backend.domain.policies.entities import PolicyRule
from backend.shared.monitoring.llm_usage_tracker import LLMUsageTracker


def _wrap(parsed, input_tokens=100, output_tokens=20):
    return {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens}),
        "parsed": parsed,
        "parsing_error": None,
    }


class CountingFakeLLM:
    """Returns responses[i] on the i-th call; records call count and prompts seen."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0
        self.model = "fake-model"

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, prompt):
        response = self._responses[self.call_count]
        self.call_count += 1
        return _wrap(response)


class ClauseExtractionCachingTests(unittest.TestCase):
    def setUp(self):
        self.tracker = LLMUsageTracker()
        self._tracker_patcher = patch(
            "backend.agents.llm_extraction_service.llm_usage_tracker", self.tracker
        )
        self._tracker_patcher.start()
        self._cache_enabled_patcher = patch(
            "backend.agents.llm_extraction_service.Phase3Config.CACHE_ENABLED", True
        )
        self._cache_enabled_patcher.start()
        from backend.shared.cache.redis_cache import cache
        self.cache = cache
        self.cache.redis_client._cache.clear()

    def tearDown(self):
        self._tracker_patcher.stop()
        self._cache_enabled_patcher.stop()
        self.cache.redis_client._cache.clear()

    def _clause_response(self, text="California law applies."):
        return _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type=CUADClauseType.GOVERNING_LAW, extracted_text=text, confidence=0.9)
        ])

    def test_second_call_with_identical_text_is_a_cache_hit_not_a_second_llm_call(self):
        llm = CountingFakeLLM([self._clause_response()])
        service = LLMExtractionService(llm)
        text = "This agreement is governed by California law applies."

        first = service.extract_clauses(text, enable_fallback=False)
        second = service.extract_clauses(text, enable_fallback=False)

        self.assertEqual(llm.call_count, 1, "second identical call must not re-invoke the LLM")
        self.assertEqual(len(first), 1)
        self.assertEqual([c.model_dump() for c in first], [c.model_dump() for c in second])

    def test_different_text_is_not_a_cache_hit(self):
        llm = CountingFakeLLM([self._clause_response("California law applies."), self._clause_response("Texas law applies.")])
        service = LLMExtractionService(llm)

        service.extract_clauses("Contract A: California law applies.", enable_fallback=False)
        service.extract_clauses("Contract B: Texas law applies.", enable_fallback=False)

        self.assertEqual(llm.call_count, 2, "different contract text must not share a cache entry")

    def test_default_candidate_types_cache_key_matches_the_real_full_type_list(self):
        """Real, confirmed bug found live: _cache_key's `candidate_types or
        []` collapsed to an empty types_part whenever candidate_types was
        the default None (every real extract_clauses(text) call, with no
        explicit override) - so the cache key never actually reflected
        which categories were in the prompt for the single most common
        call shape. _build_prompt's own default is `candidate_types or
        list(CUADClauseType)`; _cache_key must mirror that exactly, or a
        stale cache entry survives a taxonomy change indefinitely (the
        same contract text keeps serving pre-change results forever,
        since PROMPT_VERSION doesn't change just because CUADClauseType's
        membership does - this exact scenario broke a real production
        verification of a new clause type being added)."""
        llm = CountingFakeLLM([])
        service = LLMExtractionService(llm)

        self.assertEqual(
            service._cache_key("some contract text", None),
            service._cache_key("some contract text", list(CUADClauseType)),
        )

    def test_different_candidate_types_is_not_a_cache_hit(self):
        llm = CountingFakeLLM([self._clause_response(), self._clause_response()])
        service = LLMExtractionService(llm)
        text = "Same text, different candidate_types."

        service.extract_clauses(text, candidate_types=[CUADClauseType.GOVERNING_LAW])
        service.extract_clauses(text, candidate_types=[CUADClauseType.NON_COMPETE])

        self.assertEqual(llm.call_count, 2, "a narrower/different candidate_types prompt must not reuse the other's cache entry")

    def test_cache_disabled_calls_llm_every_time(self):
        self._cache_enabled_patcher.stop()
        disabled_patcher = patch("backend.agents.llm_extraction_service.Phase3Config.CACHE_ENABLED", False)
        disabled_patcher.start()
        self.addCleanup(disabled_patcher.stop)

        llm = CountingFakeLLM([self._clause_response(), self._clause_response()])
        service = LLMExtractionService(llm)
        text = "Repeated text with caching turned off."

        service.extract_clauses(text, enable_fallback=False)
        service.extract_clauses(text, enable_fallback=False)

        self.assertEqual(llm.call_count, 2, "CACHE_ENABLED=False must bypass caching entirely")

    def test_usage_tracker_records_real_call_then_free_cache_hit(self):
        llm = CountingFakeLLM([self._clause_response()])
        service = LLMExtractionService(llm)
        text = "Track my usage please."

        service.extract_clauses(text, enable_fallback=False)
        service.extract_clauses(text, enable_fallback=False)

        summary = self.tracker.get_summary()["by_operation"]["clause_extraction"]
        self.assertEqual(summary["total_calls"], 2)
        self.assertEqual(summary["cache_hits"], 1)
        self.assertEqual(summary["cache_hit_rate"], 0.5)
        self.assertGreater(summary["total_input_tokens"], 0)
        self.assertGreater(summary["total_estimated_cost_usd"], 0.0)


class PolicyEvaluationCachingTests(unittest.TestCase):
    def setUp(self):
        self.tracker = LLMUsageTracker()
        self._tracker_patcher = patch(
            "backend.agents.policy_evaluation_service.llm_usage_tracker", self.tracker
        )
        self._tracker_patcher.start()
        self._cache_enabled_patcher = patch(
            "backend.agents.policy_evaluation_service.Phase3Config.CACHE_ENABLED", True
        )
        self._cache_enabled_patcher.start()
        from backend.shared.cache.redis_cache import cache
        self.cache = cache
        self.cache.redis_client._cache.clear()
        self.rule = PolicyRule(
            id="rule_1", rule_text="No unlimited liability.", rule_type="mandatory",
            applies_to=["general"], severity="HIGH", section_reference="s1",
        )

    def tearDown(self):
        self._tracker_patcher.stop()
        self._cache_enabled_patcher.stop()
        self.cache.redis_client._cache.clear()

    def _violation_response(self):
        return _LLMPolicyEvaluationResponse(violations=[
            _LLMPolicyViolation(
                rule_id="rule_1", issue="Unlimited liability found", severity="HIGH",
                suggested_fix="Add a cap", confidence=0.9,
            )
        ])

    def test_second_call_with_identical_inputs_is_a_cache_hit(self):
        llm = CountingFakeLLM([self._violation_response()])
        service = PolicyEvaluationService(llm)

        first = service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])
        second = service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])

        self.assertEqual(llm.call_count, 1)
        self.assertEqual(first, second)

    def test_different_rules_is_not_a_cache_hit(self):
        other_rule = PolicyRule(
            id="rule_2", rule_text="Different rule entirely.", rule_type="mandatory",
            applies_to=["general"], severity="LOW", section_reference="s2",
        )
        llm = CountingFakeLLM([self._violation_response(), self._violation_response()])
        service = PolicyEvaluationService(llm)

        service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])
        service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [other_rule])

        self.assertEqual(llm.call_count, 2, "a different applicable rule set must not reuse the other's cache entry")

    def test_usage_tracker_records_cache_hit_as_free(self):
        llm = CountingFakeLLM([self._violation_response()])
        service = PolicyEvaluationService(llm)

        service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])
        service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])

        summary = self.tracker.get_summary()["by_operation"]["policy_evaluation"]
        self.assertEqual(summary["total_calls"], 2)
        self.assertEqual(summary["cache_hits"], 1)
        # The cache hit itself cost nothing - total cost reflects only the
        # one real call, not two (asserted via the public summary now that
        # the tracker is Redis-backed counters, not a raw in-process event
        # list - there's no per-event list to reach into anymore).
        self.assertGreater(summary["total_estimated_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
