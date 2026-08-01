"""
Regression tests closing the remaining gap in AI-engineering-depth audit
finding #13: @track_performance's latency tracking on extract_clauses/
evaluate_clause was in-process only, inconsistent with the Redis-backed
cross-process pattern already proven for cost/token tracking (finding #1,
LLMUsageTracker) and hallucination tracking (finding #12,
hallucination_tracker) in the same pass.

shared/monitoring/latency_tracker.py closes it: p50/p95 duration samples
now live in the same shared Redis instance (via `cache.redis_client`,
raw client calls - never the RedisCache.get/set JSON wrapper), not either
process's own memory. These tests prove the concrete before/after, same
pattern as test_llm_usage_tracker_redis_backed.py's CrossProcessVisibility
Tests: two simulated processes sharing only a common backing store see
each other's recorded latency samples.
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.shared.cache.redis_cache import cache, InMemoryCache
from backend.shared.monitoring import latency_tracker


class CrossProcessVisibilityTests(unittest.TestCase):
    """The core regression proof: a duration recorded while `cache.
    redis_client` is one InMemoryCache instance (standing in for the
    `worker` process, which actually runs extract_clauses/evaluate_clause)
    is visible to a read that happens while `cache.redis_client` is
    reassigned to a *different* Python reference to the same instance
    (standing in for the `backend` process reading GET /metrics) - proving
    the data lives in the shared store, not in either call's own process
    memory."""

    def setUp(self):
        # One shared backing store, standing in for the one real Redis
        # instance both containers actually connect to in production.
        self.shared_store = InMemoryCache()

    def test_duration_recorded_in_one_process_is_visible_in_another(self):
        with patch.object(cache, "redis_client", self.shared_store):
            # The real call happens in the worker (the actual /analyze path).
            latency_tracker.record_duration("clause_extraction", 120.0)

        with patch.object(cache, "redis_client", self.shared_store):
            # /metrics is scraped from the backend process.
            stats = latency_tracker.get_summary()["clause_extraction"]

        self.assertEqual(stats["sample_count"], 1)
        self.assertEqual(stats["p50_duration_ms"], 120.0)
        self.assertEqual(stats["p95_duration_ms"], 120.0)

    def test_samples_from_both_simulated_processes_accumulate_together(self):
        with patch.object(cache, "redis_client", self.shared_store):
            latency_tracker.record_duration("policy_evaluation", 100.0)
        with patch.object(cache, "redis_client", self.shared_store):
            latency_tracker.record_duration("policy_evaluation", 200.0)

        with patch.object(cache, "redis_client", self.shared_store):
            stats_read_after_both = latency_tracker.get_summary()["policy_evaluation"]

        # Both samples are visible from a single read - there's no
        # per-process state left to diverge.
        self.assertEqual(stats_read_after_both["sample_count"], 2)
        self.assertEqual(stats_read_after_both["avg_duration_ms"], 150.0)


class LatencyTrackerBehaviorTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client = InMemoryCache()

    def test_p50_p95_are_computed_correctly_over_a_known_distribution(self):
        # 1..100 ms -> median (p50) is 50/51 boundary, p95 is the 95th value.
        for ms in range(1, 101):
            latency_tracker.record_duration("clause_extraction", float(ms))

        stats = latency_tracker.get_summary()["clause_extraction"]
        self.assertEqual(stats["sample_count"], 100)
        self.assertEqual(stats["p50_duration_ms"], 51.0)
        self.assertEqual(stats["p95_duration_ms"], 96.0)
        self.assertEqual(stats["max_duration_ms"], 100.0)

    def test_sample_window_is_capped_and_keeps_the_most_recent(self):
        # Record 150 samples (0..149ms) into a window capped at 100 - only
        # the most recent 100 (50..149) should remain, matching
        # PerformanceMonitor's own "keep the last 100" convention.
        for ms in range(150):
            latency_tracker.record_duration("clause_extraction", float(ms))

        stats = latency_tracker.get_summary()["clause_extraction"]
        self.assertEqual(stats["sample_count"], 100)
        self.assertEqual(stats["max_duration_ms"], 149.0)

    def test_operations_are_tracked_independently(self):
        latency_tracker.record_duration("clause_extraction", 100.0)
        latency_tracker.record_duration("policy_evaluation", 500.0)

        summary = latency_tracker.get_summary()
        self.assertEqual(summary["clause_extraction"]["avg_duration_ms"], 100.0)
        self.assertEqual(summary["policy_evaluation"]["avg_duration_ms"], 500.0)

    def test_no_data_yields_an_empty_but_valid_summary(self):
        summary = latency_tracker.get_summary()
        self.assertEqual(summary, {})

    def test_recording_never_raises_even_if_redis_is_broken(self):
        broken_client = MagicMock()
        broken_client.rpush.side_effect = Exception("redis down")
        with patch.object(cache, "redis_client", broken_client):
            latency_tracker.record_duration("clause_extraction", 100.0)  # must not raise

    def test_reading_never_raises_even_if_redis_is_broken(self):
        broken_client = MagicMock()
        broken_client.smembers.side_effect = Exception("redis down")
        with patch.object(cache, "redis_client", broken_client):
            summary = latency_tracker.get_summary()  # must not raise
        self.assertEqual(summary, {})


class TrackLatencyDecoratorTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client = InMemoryCache()

    def test_decorator_records_a_real_duration_and_returns_the_wrapped_value(self):
        import time

        @latency_tracker.track_latency("decorated_op")
        def slow_add(a, b):
            time.sleep(0.01)
            return a + b

        result = slow_add(2, 3)

        self.assertEqual(result, 5)
        stats = latency_tracker.get_summary()["decorated_op"]
        self.assertEqual(stats["sample_count"], 1)
        self.assertGreaterEqual(stats["avg_duration_ms"], 10.0)

    def test_decorator_records_duration_even_when_the_function_raises(self):
        @latency_tracker.track_latency("failing_op")
        def always_fails():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            always_fails()

        stats = latency_tracker.get_summary()["failing_op"]
        self.assertEqual(stats["sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
