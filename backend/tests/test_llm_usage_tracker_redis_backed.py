"""
Regression tests for production-readiness audit finding #1: LLMUsageTracker
was in-process-only, and the Celery migration (backend/tasks.py) made it
blind to almost all real cost - the LLM calls it records now run inside
the separate `worker` container, while GET /api/monitoring/llm-usage is
served by the `backend` container. Two OS processes, two copies of
Python's memory.

Fixed by moving counters into Redis (shared/cache/redis_cache.py's `cache`
singleton, already deployed). These tests prove the concrete before/after:
two independently-constructed LLMUsageTracker instances, sharing only a
common backing store (standing in for "backend process" and "worker
process" each importing this module fresh), see each other's recorded
usage - the exact scenario that was broken.
"""

import unittest

from backend.shared.cache.redis_cache import InMemoryCache
from backend.shared.monitoring.llm_usage_tracker import LLMUsageTracker


class CrossProcessVisibilityTests(unittest.TestCase):
    """The core regression proof: usage recorded by one tracker instance
    is visible to a completely separate tracker instance, as long as they
    share a backing store - modeling backend vs. worker, which share real
    Redis but not Python memory."""

    def setUp(self):
        # One shared backing store, standing in for the one real Redis
        # instance both containers actually connect to in production.
        self.shared_store = InMemoryCache()

    def test_usage_recorded_by_one_instance_is_visible_to_another(self):
        backend_process_tracker = LLMUsageTracker(redis_client=self.shared_store)
        worker_process_tracker = LLMUsageTracker(redis_client=self.shared_store)

        # The real call happens in the worker (the actual /analyze path).
        worker_process_tracker.record_call(
            "clause_extraction", "gemini-2.5-flash", cache_hit=False,
            usage_metadata={"input_tokens": 1000, "output_tokens": 200},
        )

        # The dashboard is read from the backend process.
        summary = backend_process_tracker.get_summary()["by_operation"]["clause_extraction"]
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["total_input_tokens"], 1000)
        self.assertEqual(summary["total_output_tokens"], 200)
        self.assertGreater(summary["total_estimated_cost_usd"], 0.0)

    def test_calls_from_both_simulated_processes_accumulate_together(self):
        backend_process_tracker = LLMUsageTracker(redis_client=self.shared_store)
        worker_process_tracker = LLMUsageTracker(redis_client=self.shared_store)

        worker_process_tracker.record_call(
            "policy_evaluation", "gemini-2.5-flash", cache_hit=False,
            usage_metadata={"input_tokens": 500, "output_tokens": 50},
        )
        backend_process_tracker.record_call(
            "policy_evaluation", "gemini-2.5-flash", cache_hit=True,
            usage_metadata={"input_tokens": 500, "output_tokens": 50},
        )

        # Either instance reads the combined total - there's no
        # per-instance state left to diverge.
        summary_from_worker = worker_process_tracker.get_summary()["by_operation"]["policy_evaluation"]
        summary_from_backend = backend_process_tracker.get_summary()["by_operation"]["policy_evaluation"]
        self.assertEqual(summary_from_worker, summary_from_backend)
        self.assertEqual(summary_from_worker["total_calls"], 2)
        self.assertEqual(summary_from_worker["cache_hits"], 1)


class LLMUsageTrackerBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tracker = LLMUsageTracker(redis_client=InMemoryCache())

    def test_cache_hit_records_zero_cost(self):
        self.tracker.record_call(
            "clause_extraction", "gemini-2.5-flash", cache_hit=True,
            usage_metadata={"input_tokens": 1000, "output_tokens": 200},
        )
        summary = self.tracker.get_summary()["by_operation"]["clause_extraction"]
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["cache_hits"], 1)
        self.assertEqual(summary["total_estimated_cost_usd"], 0.0)

    def test_overall_sums_across_operations(self):
        self.tracker.record_call(
            "clause_extraction", "gemini-2.5-flash", cache_hit=False,
            usage_metadata={"input_tokens": 100, "output_tokens": 10},
        )
        self.tracker.record_call(
            "policy_evaluation", "gemini-2.5-flash", cache_hit=False,
            usage_metadata={"input_tokens": 200, "output_tokens": 20},
        )

        overall = self.tracker.get_summary()["overall"]
        self.assertEqual(overall["total_calls"], 2)
        self.assertEqual(overall["total_input_tokens"], 300)
        self.assertEqual(overall["total_output_tokens"], 30)

    def test_no_calls_recorded_yields_empty_but_valid_summary(self):
        summary = self.tracker.get_summary()
        self.assertEqual(summary["overall"]["total_calls"], 0)
        self.assertEqual(summary["overall"]["cache_hit_rate"], 0.0)
        self.assertEqual(summary["by_operation"], {})

    def test_tracker_failure_does_not_raise(self):
        """Usage tracking must never break the real call it's observing."""
        class BrokenClient:
            def sadd(self, *a, **k):
                raise ConnectionError("Redis is down")

        tracker = LLMUsageTracker(redis_client=BrokenClient())
        try:
            tracker.record_call(
                "clause_extraction", "gemini-2.5-flash", cache_hit=False,
                usage_metadata={"input_tokens": 100, "output_tokens": 10},
            )
        except Exception as e:
            self.fail(f"record_call must not raise on a tracking failure: {e}")


if __name__ == "__main__":
    unittest.main()
