"""
Regression tests for the real circuit breaker (backend/shared/reliability/
circuit_breaker.py), built to replace the previous one - removed earlier
in this engagement as confirmed non-functional (constructed fresh per
request, so its failure count could never accumulate past a single call
and the breaker could never actually open).

This one is Redis-backed (InMemoryCache fallback in tests, same pattern as
hallucination_tracker.py/llm_usage_tracker.py), so state genuinely
persists across separate `CircuitBreaker(...)` instances/calls - the same
cross-process durability those trackers rely on. Tests construct fresh,
uniquely-named CircuitBreaker instances per test (not the shared GEMINI_/
NEO4J_ singletons) so state from one test can't leak into another, and
clear the shared InMemoryCache store in setUp/tearDown for the same reason
test_llm_extraction_caching.py does.
"""

import time
import unittest
from unittest.mock import patch

from backend.shared.cache.redis_cache import cache
from backend.shared.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)


class CircuitBreakerStateMachineTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client._cache.clear()

    def tearDown(self):
        cache.redis_client._cache.clear()

    def test_starts_closed_and_allows_requests(self):
        breaker = CircuitBreaker("test_starts_closed", failure_threshold=3, recovery_timeout_seconds=10)
        self.assertEqual(breaker.get_status()["state"], CircuitState.CLOSED.value)
        self.assertTrue(breaker.allow_request())

    def test_opens_after_reaching_the_failure_threshold(self):
        breaker = CircuitBreaker("test_opens_after_threshold", failure_threshold=3, recovery_timeout_seconds=10)

        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.get_status()["state"], CircuitState.CLOSED.value, "must stay closed below threshold")
        self.assertTrue(breaker.allow_request())

        breaker.record_failure()  # 3rd consecutive failure hits the threshold
        self.assertEqual(breaker.get_status()["state"], CircuitState.OPEN.value)

    def test_open_breaker_blocks_requests_without_calling_anything(self):
        breaker = CircuitBreaker("test_open_blocks", failure_threshold=1, recovery_timeout_seconds=60)
        breaker.record_failure()  # trips open immediately (threshold=1)

        self.assertFalse(breaker.allow_request())
        with self.assertRaises(CircuitBreakerOpenError):
            with breaker.guard():
                self.fail("the guarded block must never execute while the breaker is open")

    def test_a_success_resets_the_failure_count_while_closed(self):
        breaker = CircuitBreaker("test_success_resets", failure_threshold=3, recovery_timeout_seconds=10)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()

        self.assertEqual(breaker.get_status()["failure_count"], 0)
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.get_status()["state"], CircuitState.CLOSED.value, "2 failures after a reset must not trip a threshold-3 breaker")

    def test_moves_to_half_open_after_recovery_timeout_elapses(self):
        breaker = CircuitBreaker("test_half_open_transition", failure_threshold=1, recovery_timeout_seconds=30)
        breaker.record_failure()  # OPEN
        self.assertFalse(breaker.allow_request())

        with patch("backend.shared.reliability.circuit_breaker.time.time", return_value=time.time() + 31):
            self.assertTrue(breaker.allow_request(), "recovery_timeout_seconds elapsed - must allow the HALF_OPEN trial")
            self.assertEqual(breaker.get_status()["state"], CircuitState.HALF_OPEN.value)

    def test_half_open_trial_success_closes_the_breaker(self):
        breaker = CircuitBreaker("test_half_open_success_closes", failure_threshold=1, recovery_timeout_seconds=30)
        breaker.record_failure()  # OPEN
        with patch("backend.shared.reliability.circuit_breaker.time.time", return_value=time.time() + 31):
            self.assertTrue(breaker.allow_request())  # -> HALF_OPEN

        breaker.record_success()

        self.assertEqual(breaker.get_status()["state"], CircuitState.CLOSED.value)
        self.assertEqual(breaker.get_status()["failure_count"], 0)
        self.assertTrue(breaker.allow_request())

    def test_half_open_trial_failure_reopens_immediately(self):
        breaker = CircuitBreaker("test_half_open_failure_reopens", failure_threshold=5, recovery_timeout_seconds=30)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()  # 5th -> OPEN

        with patch("backend.shared.reliability.circuit_breaker.time.time", return_value=time.time() + 31):
            self.assertTrue(breaker.allow_request())  # -> HALF_OPEN
            breaker.record_failure()  # the trial call itself failed

        self.assertEqual(breaker.get_status()["state"], CircuitState.OPEN.value, "a failed trial must reopen, not require another full threshold")
        self.assertFalse(breaker.allow_request())

    def test_guard_records_success_on_clean_exit(self):
        breaker = CircuitBreaker("test_guard_success", failure_threshold=3, recovery_timeout_seconds=10)
        breaker.record_failure()
        breaker.record_failure()  # below threshold=3 - still closed, guard() must run the block

        with breaker.guard():
            pass  # a call that "succeeds"

        self.assertEqual(breaker.get_status()["failure_count"], 0)

    def test_guard_records_failure_and_reraises_the_original_exception(self):
        breaker = CircuitBreaker("test_guard_failure", failure_threshold=5, recovery_timeout_seconds=10)

        with self.assertRaises(ValueError):
            with breaker.guard():
                raise ValueError("the real dependency failed")

        self.assertEqual(breaker.get_status()["failure_count"], 1)

    def test_guard_does_not_swallow_circuit_breaker_open_error_as_a_new_failure(self):
        # Once open, repeatedly attempting guard() must not keep incrementing
        # failure_count past the threshold - it should just keep rejecting.
        breaker = CircuitBreaker("test_guard_open_no_double_count", failure_threshold=1, recovery_timeout_seconds=60)
        breaker.record_failure()  # OPEN, failure_count == 1

        for _ in range(3):
            with self.assertRaises(CircuitBreakerOpenError):
                with breaker.guard():
                    self.fail("must never execute")

        self.assertEqual(breaker.get_status()["failure_count"], 1, "rejections while open must not themselves count as new failures")


if __name__ == "__main__":
    unittest.main()
