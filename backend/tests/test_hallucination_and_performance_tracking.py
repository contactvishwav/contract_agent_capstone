"""
Regression tests for AI-engineering-depth audit findings #12 and #13.

#12 - ClauseDetectorTool's ungrounded_count (intelligence_tools.py) and
PolicyEvaluationService.evaluate_clause's hallucinated-citation discards
were both log-only, with no persisted rate anyone could actually query.
Both are now wired into shared/monitoring/hallucination_tracker.py - the
same Redis-backed, cross-process-visible pattern as finding #1's
LLMUsageTracker (extraction/evaluation run in the `worker` container;
GET /api/monitoring/llm-usage and GET /metrics are served by `backend`).

#13 - LLMExtractionService.extract_clauses and PolicyEvaluationService.
evaluate_clause - the two calls that dominate real per-contract cost/
latency - had no latency tracking at all, unlike the secondary
CUAD-mitigation tools in optimized_cuad_tools.py (which use the
in-process @track_performance). Both now carry @track_latency
(shared/monitoring/latency_tracker.py) - the Redis-backed, cross-process-
visible counterpart, matching findings #1/#12's pattern rather than the
in-process one, since these calls run in the `worker` container while
GET /metrics is served by `backend`. Deep correctness/cross-process
coverage for the tracker itself lives in
test_latency_tracker_redis_backed.py; this file just proves the two real
calls are actually wired to it.
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.intelligence_tools import ClauseDetectorTool
    from backend.agents.llm_extraction_service import (
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
    from backend.shared.cache.redis_cache import cache, InMemoryCache
    from backend.shared.monitoring import hallucination_tracker
    from backend.shared.monitoring import latency_tracker


def _wrap(parsed, input_tokens=100, output_tokens=20):
    from types import SimpleNamespace
    return {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens}),
        "parsed": parsed,
        "parsing_error": None,
    }


class CountingFakeLLM:
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


class HallucinationTrackerUnitTests(unittest.TestCase):
    """Direct correctness proof for the tracking module itself, before
    involving any of its real callers."""

    def setUp(self):
        cache.redis_client = InMemoryCache()

    def test_rate_calculation_is_correct(self):
        hallucination_tracker.record("clause_extraction", total=10, flagged=3)
        summary = hallucination_tracker.get_summary()
        stats = summary["by_category"]["clause_extraction"]
        self.assertEqual(stats["total"], 10)
        self.assertEqual(stats["flagged"], 3)
        self.assertAlmostEqual(stats["rate"], 0.3)

    def test_rate_accumulates_across_multiple_record_calls(self):
        hallucination_tracker.record("clause_extraction", total=10, flagged=2)
        hallucination_tracker.record("clause_extraction", total=10, flagged=4)
        stats = hallucination_tracker.get_summary()["by_category"]["clause_extraction"]
        self.assertEqual(stats["total"], 20)
        self.assertEqual(stats["flagged"], 6)
        self.assertAlmostEqual(stats["rate"], 0.3)

    def test_categories_are_tracked_independently(self):
        hallucination_tracker.record("clause_extraction", total=10, flagged=1)
        hallucination_tracker.record("policy_citation", total=4, flagged=2)

        summary = hallucination_tracker.get_summary()
        self.assertAlmostEqual(summary["by_category"]["clause_extraction"]["rate"], 0.1)
        self.assertAlmostEqual(summary["by_category"]["policy_citation"]["rate"], 0.5)
        # Overall combines both categories, not just the last one recorded.
        self.assertEqual(summary["overall"]["total"], 14)
        self.assertEqual(summary["overall"]["flagged"], 3)

    def test_zero_flagged_gives_zero_rate_not_a_division_error(self):
        hallucination_tracker.record("clause_extraction", total=5, flagged=0)
        stats = hallucination_tracker.get_summary()["by_category"]["clause_extraction"]
        self.assertEqual(stats["rate"], 0.0)

    def test_no_data_gives_zero_rate_not_a_division_error(self):
        summary = hallucination_tracker.get_summary()
        self.assertEqual(summary["overall"], {"total": 0, "flagged": 0, "rate": 0.0})

    def test_recording_zero_total_is_a_noop(self):
        hallucination_tracker.record("clause_extraction", total=0, flagged=0)
        summary = hallucination_tracker.get_summary()
        self.assertEqual(summary["by_category"], {})

    def test_two_independent_readers_share_state(self):
        """Cross-process visibility proof, mirroring finding #1's own
        CrossProcessVisibilityTests."""
        shared_backing_store = InMemoryCache()
        with patch.object(cache, "redis_client", shared_backing_store):
            hallucination_tracker.record("clause_extraction", total=10, flagged=5)

        with patch.object(cache, "redis_client", shared_backing_store):
            summary = hallucination_tracker.get_summary()

        self.assertAlmostEqual(summary["by_category"]["clause_extraction"]["rate"], 0.5)

    def test_recording_never_raises_even_if_redis_is_broken(self):
        broken_client = MagicMock()
        broken_client.sadd.side_effect = Exception("redis down")
        with patch.object(cache, "redis_client", broken_client):
            hallucination_tracker.record("clause_extraction", total=5, flagged=1)  # must not raise

    def test_reading_never_raises_even_if_redis_is_broken(self):
        broken_client = MagicMock()
        broken_client.smembers.side_effect = Exception("redis down")
        with patch.object(cache, "redis_client", broken_client):
            summary = hallucination_tracker.get_summary()  # must not raise
        self.assertEqual(summary["overall"], {"total": 0, "flagged": 0, "rate": 0.0})


class ClauseDetectorToolRecordsUngroundedRateTests(unittest.TestCase):
    """End-to-end proof: running the real tool - not calling
    hallucination_tracker directly - actually records grounding data.

    AuditLogger() (called separately, inline inside _run) is mocked out
    here - it lazily constructs a real Neo4jContractRepository on first
    use, which is audit-logging's own concern (see
    test_intelligence_tool_audit_logging.py) and orthogonal to grounding
    tracking, which happens before that call regardless of its outcome."""

    def setUp(self):
        cache.redis_client = InMemoryCache()
        self._audit_patcher = patch("backend.agents.intelligence_tools.AuditLogger")
        self._audit_patcher.start()
        self.addCleanup(self._audit_patcher.stop)

    def test_ungrounded_extraction_is_recorded(self):
        # One clause whose extracted_text cannot be found in the source
        # text at all -> _find_span returns (-1, -1) -> ungrounded.
        response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.GOVERNING_LAW,
                extracted_text="This exact phrase is not anywhere in the source text.",
                confidence=0.8,
            )
        ])
        llm = CountingFakeLLM([response])
        tool = ClauseDetectorTool(llm=llm)

        tool._run(contract_text="A totally different contract body.", contract_id="c1", tenant_id="t1")

        stats = hallucination_tracker.get_summary()["by_category"]["clause_extraction"]
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["flagged"], 1)
        self.assertEqual(stats["rate"], 1.0)

    def test_grounded_extraction_is_recorded_with_zero_flagged(self):
        text = "This agreement is governed by California law."
        response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.GOVERNING_LAW,
                extracted_text="governed by California law",
                confidence=0.9,
            )
        ])
        llm = CountingFakeLLM([response])
        tool = ClauseDetectorTool(llm=llm)

        tool._run(contract_text=text, contract_id="c1", tenant_id="t1")

        stats = hallucination_tracker.get_summary()["by_category"]["clause_extraction"]
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["flagged"], 0)
        self.assertEqual(stats["rate"], 0.0)


class PolicyEvaluationServiceRecordsCitationDiscardRateTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client = InMemoryCache()
        self.rule = PolicyRule(
            id="rule_1", rule_text="No unlimited liability.", rule_type="mandatory",
            applies_to=["general"], severity="HIGH", section_reference="s1",
        )

    def test_hallucinated_citation_is_recorded_as_flagged(self):
        # rule_id "does_not_exist" was never offered to the model - a real
        # hallucinated citation, discarded by evaluate_clause itself.
        response = _LLMPolicyEvaluationResponse(violations=[
            _LLMPolicyViolation(
                rule_id="does_not_exist", issue="fabricated", severity="HIGH",
                suggested_fix="n/a", confidence=0.9,
            )
        ])
        llm = CountingFakeLLM([response])
        service = PolicyEvaluationService(llm)

        violations = service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])

        self.assertEqual(violations, [])  # discarded, not returned
        stats = hallucination_tracker.get_summary()["by_category"]["policy_citation"]
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["flagged"], 1)
        self.assertEqual(stats["rate"], 1.0)

    def test_valid_citation_is_recorded_with_zero_flagged(self):
        response = _LLMPolicyEvaluationResponse(violations=[
            _LLMPolicyViolation(
                rule_id="rule_1", issue="real violation", severity="HIGH",
                suggested_fix="add a cap", confidence=0.9,
            )
        ])
        llm = CountingFakeLLM([response])
        service = PolicyEvaluationService(llm)

        violations = service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])

        self.assertEqual(len(violations), 1)
        stats = hallucination_tracker.get_summary()["by_category"]["policy_citation"]
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["flagged"], 0)

    def test_no_violations_returned_records_nothing(self):
        response = _LLMPolicyEvaluationResponse(violations=[])
        llm = CountingFakeLLM([response])
        service = PolicyEvaluationService(llm)

        service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [self.rule])

        summary = hallucination_tracker.get_summary()
        self.assertEqual(summary["by_category"], {})


class PrimaryPathPerformanceTrackingTests(unittest.TestCase):
    """Finding #13: extract_clauses/evaluate_clause now carry
    @track_latency (Redis-backed - see test_latency_tracker_redis_backed.py
    for the tracker's own correctness/cross-process coverage), so real
    p50/p95 is reported for the primary path regardless of which
    container ran the call."""

    def setUp(self):
        cache.redis_client = InMemoryCache()

    def test_extract_clauses_records_a_latency_sample(self):
        from backend.agents.llm_extraction_service import LLMExtractionService

        response = _LLMExtractionResponse(clauses=[])
        llm = CountingFakeLLM([response])
        service = LLMExtractionService(llm)

        service.extract_clauses("Some contract text with no matching clauses.")

        stats = latency_tracker.get_summary()["clause_extraction"]
        self.assertEqual(stats["sample_count"], 1)
        self.assertIn("p50_duration_ms", stats)
        self.assertIn("p95_duration_ms", stats)

    def test_evaluate_clause_records_a_latency_sample(self):
        rule = PolicyRule(
            id="rule_1", rule_text="No unlimited liability.", rule_type="mandatory",
            applies_to=["general"], severity="HIGH", section_reference="s1",
        )
        response = _LLMPolicyEvaluationResponse(violations=[])
        llm = CountingFakeLLM([response])
        service = PolicyEvaluationService(llm)

        service.evaluate_clause("Cap On Liability", "Liability is unlimited.", [rule])

        stats = latency_tracker.get_summary()["policy_evaluation"]
        self.assertEqual(stats["sample_count"], 1)
        self.assertIn("p50_duration_ms", stats)
        self.assertIn("p95_duration_ms", stats)


if __name__ == "__main__":
    unittest.main()
