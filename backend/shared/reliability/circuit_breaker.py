"""
Real, Redis-backed circuit breaker protecting the two external
dependencies that actually fail in production: Gemini API calls
(LLMExtractionService.extract_clauses, PolicyEvaluationService.
evaluate_clause) and Neo4j calls (Neo4jContractRepository).

Replaces the previous circuit breaker, removed earlier in this engagement
as confirmed non-functional: it was constructed fresh on every request, so
its failure counter could never accumulate past a single call and the
breaker could never actually open. This one persists its state in the
same shared Redis instance already used for caching and for cost/
hallucination tracking (backend/shared/cache/redis_cache.py,
llm_usage_tracker.py, hallucination_tracker.py) - state is keyed by
breaker name, not by process, so it is shared across every backend and
Celery worker container and survives across individual requests.

Real CLOSED -> OPEN -> HALF_OPEN state machine:
- CLOSED: calls pass through normally. `failure_threshold` consecutive
  failures (any success resets the counter to 0) trips the breaker OPEN.
- OPEN: calls are rejected immediately via CircuitBreakerOpenError,
  without ever reaching the failing dependency, until
  `recovery_timeout_seconds` have elapsed since it opened - at which
  point exactly the next call is let through as a single HALF_OPEN trial.
- HALF_OPEN: that trial call's own outcome decides what happens next -
  success closes the breaker (failure count reset to 0); failure reopens
  it immediately with a fresh opened_at timestamp.
"""

import time
from contextlib import contextmanager
from enum import Enum

from backend.shared.cache.redis_cache import cache
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "circuit_breaker"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised in place of attempting a call while the breaker is OPEN -
    the dependency is never actually contacted."""

    def __init__(self, name: str, retry_after_seconds: float):
        self.name = name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Circuit breaker '{name}' is open - failing fast without calling "
            f"the dependency. Retry after {retry_after_seconds:.1f}s."
        )


class CircuitBreaker:
    """One instance per protected dependency (e.g. "gemini", "neo4j").
    All state lives in Redis under this instance's `name`, never in
    process memory - this is what makes the breaker actually work across
    requests and processes, unlike the removed fresh-per-request one."""

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout_seconds: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds

    def _client(self):
        return cache.redis_client

    def _key(self, field: str) -> str:
        return f"{_KEY_PREFIX}:{self.name}:{field}"

    def _get_state(self) -> CircuitState:
        try:
            value = self._client().get(self._key("state"))
        except Exception as e:
            logger.warning(f"Circuit breaker '{self.name}': failed to read state, defaulting to closed: {e}")
            return CircuitState.CLOSED
        try:
            return CircuitState(value) if value else CircuitState.CLOSED
        except ValueError:
            return CircuitState.CLOSED

    def _opened_at(self) -> float:
        try:
            value = self._client().get(self._key("opened_at"))
            return float(value) if value else 0.0
        except Exception:
            return 0.0

    def allow_request(self) -> bool:
        """True if a call should be attempted now; False if it must be
        rejected without ever reaching the dependency."""
        state = self._get_state()

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            elapsed = time.time() - self._opened_at()
            if elapsed >= self.recovery_timeout_seconds:
                self._set_state(CircuitState.HALF_OPEN)
                return True
            return False

        # HALF_OPEN: exactly this one trial call is let through; its
        # outcome (record_success/record_failure) decides what's next.
        return True

    def record_success(self) -> None:
        try:
            client = self._client()
            client.set(self._key("failure_count"), 0)
            client.set(self._key("state"), CircuitState.CLOSED.value)
        except Exception as e:
            logger.warning(f"Circuit breaker '{self.name}': failed to record success: {e}")

    def record_failure(self) -> None:
        try:
            client = self._client()
            state = self._get_state()
            if state == CircuitState.HALF_OPEN:
                # The trial call itself failed - reopen immediately rather
                # than accumulating toward failure_threshold again.
                self._trip_open(client)
                return

            failure_count = client.incr(self._key("failure_count"))
            if failure_count >= self.failure_threshold:
                self._trip_open(client)
        except Exception as e:
            logger.warning(f"Circuit breaker '{self.name}': failed to record failure: {e}")

    def _trip_open(self, client) -> None:
        client.set(self._key("state"), CircuitState.OPEN.value)
        client.set(self._key("opened_at"), time.time())
        logger.warning(
            f"Circuit breaker '{self.name}': OPEN - failing fast for {self.recovery_timeout_seconds}s"
        )

    def _set_state(self, state: CircuitState) -> None:
        try:
            self._client().set(self._key("state"), state.value)
        except Exception as e:
            logger.warning(f"Circuit breaker '{self.name}': failed to set state to {state}: {e}")

    def get_status(self) -> dict:
        state = self._get_state()
        try:
            failure_count = int(self._client().get(self._key("failure_count")) or 0)
        except Exception:
            failure_count = 0
        return {
            "name": self.name,
            "state": state.value,
            "failure_count": failure_count,
            "failure_threshold": self.failure_threshold,
        }

    @contextmanager
    def guard(self):
        """Usage: `with breaker.guard(): result = risky_call()`.

        Raises CircuitBreakerOpenError - without ever running the `with`
        block - if the breaker is currently open. Otherwise runs the
        block; any exception it raises is recorded as a failure and then
        re-raised unchanged (never suppressed), and a clean return is
        recorded as a success.
        """
        if not self.allow_request():
            retry_after = max(0.0, self.recovery_timeout_seconds - (time.time() - self._opened_at()))
            raise CircuitBreakerOpenError(self.name, retry_after)
        try:
            yield
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()


# Module-level singletons: the two external dependencies actually
# implicated in production failures during this engagement.
GEMINI_CIRCUIT_BREAKER = CircuitBreaker("gemini", failure_threshold=5, recovery_timeout_seconds=30.0)
NEO4J_CIRCUIT_BREAKER = CircuitBreaker("neo4j", failure_threshold=5, recovery_timeout_seconds=15.0)

# Deliberately a separate instance from GEMINI_CIRCUIT_BREAKER above, not a
# reuse of it, even though both protect the same underlying Gemini API:
# search re-ranking (backend/agents/reranker_service.py) is a best-effort
# UX enhancement on a synchronous, user-facing request path, so it runs
# under a much stricter timeout (a few seconds) than clause extraction/
# policy evaluation's 120s budget. Under real network jitter, that tighter
# timeout will trip *more often* than extraction/policy's own failure rate
# even when Gemini itself is perfectly healthy - sharing a breaker would
# let re-ranking's stricter budget spuriously open the breaker that also
# guards extraction and policy evaluation, degrading something load-bearing
# because of something optional. Same CLOSED/OPEN/HALF_OPEN mechanism,
# same Redis-backed persistence, independent failure domain.
RERANKER_CIRCUIT_BREAKER = CircuitBreaker("gemini_reranker", failure_threshold=5, recovery_timeout_seconds=30.0)

# Real multi-provider fallback (backend/agents/llm_fallback_service.py),
# added the same night a real production incident (Gemini was the only
# configured provider, and its only working model hit a real daily quota
# exhaustion) took down PDF upload/analysis entirely. One breaker per
# fallback provider, independent of GEMINI_CIRCUIT_BREAKER and of each
# other - the whole point of falling back to OpenAI/Anthropic is that a
# Gemini-specific outage (or, as happened tonight, a Gemini-specific
# billing problem) must not also affect whether OpenAI/Anthropic are
# considered healthy, and vice versa. Used by the extraction/policy-
# evaluation fallback chain (120s budget, matching GEMINI_CIRCUIT_BREAKER).
OPENAI_CIRCUIT_BREAKER = CircuitBreaker("openai", failure_threshold=5, recovery_timeout_seconds=30.0)
ANTHROPIC_CIRCUIT_BREAKER = CircuitBreaker("anthropic", failure_threshold=5, recovery_timeout_seconds=30.0)

# Separate OpenAI/Anthropic breakers for the re-ranking fallback chain,
# mirroring RERANKER_CIRCUIT_BREAKER's isolation from GEMINI_CIRCUIT_BREAKER
# above for the exact same reason: re-ranking's short timeout budget trips
# more often than extraction/policy's own failure rate even when a
# provider is perfectly healthy, so it must never share a failure count
# with (and potentially spuriously open) the breaker guarding the
# load-bearing extraction/policy-evaluation fallback chain.
RERANKER_OPENAI_CIRCUIT_BREAKER = CircuitBreaker("openai_reranker", failure_threshold=5, recovery_timeout_seconds=30.0)
RERANKER_ANTHROPIC_CIRCUIT_BREAKER = CircuitBreaker("anthropic_reranker", failure_threshold=5, recovery_timeout_seconds=30.0)
