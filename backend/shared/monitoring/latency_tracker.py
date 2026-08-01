"""
Redis-backed p50/p95 latency tracking for LLMExtractionService.
extract_clauses and PolicyEvaluationService.evaluate_clause (AI-
engineering-depth audit finding #13, closed further per follow-up).

@track_performance (performance_monitor.py) is in-process only - fine for
the secondary CUAD-mitigation tools it already covered, but these two
calls run in the Celery `worker` container while GET /metrics is served
by `backend`. Same cross-process blind spot findings #1 (LLMUsageTracker)
and #12 (hallucination_tracker) already fixed for cost/token and
hallucination-rate tracking in this same pass - this module closes it for
latency too, using the identical Redis-backed pattern (raw client calls
on `cache.redis_client`, never the RedisCache.get/set JSON wrapper).

Raw duration samples are kept in a capped Redis LIST per operation
(RPUSH + LTRIM to the most recent _MAX_SAMPLES), mirroring
PerformanceMonitor's own "keep the last 100 metrics per operation"
convention - just shared across processes instead of held in one
process's memory. p50/p95 are computed over whatever samples are
currently in that shared window, so they update the moment either
process contributes and stay visible to both.
"""

from typing import Any, Callable, Dict
from functools import wraps
import time

from backend.shared.cache.redis_cache import cache
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "latency_tracking"
_OPERATIONS_KEY = f"{_KEY_PREFIX}:operations"
_MAX_SAMPLES = 100

_EMPTY_STATS = {
    "sample_count": 0,
    "avg_duration_ms": 0.0,
    "p50_duration_ms": 0.0,
    "p95_duration_ms": 0.0,
    "max_duration_ms": 0.0,
}


def _client():
    return cache.redis_client


def record_duration(operation: str, duration_ms: float) -> None:
    """Never raises - a tracking bug must never break the real call it's
    observing, matching llm_usage_tracker/hallucination_tracker's own
    failure-handling convention."""
    try:
        client = _client()
        client.sadd(_OPERATIONS_KEY, operation)
        key = f"{_KEY_PREFIX}:{operation}:samples"
        client.rpush(key, duration_ms)
        client.ltrim(key, -_MAX_SAMPLES, -1)
    except Exception as e:
        logger.warning(f"Failed to record latency for operation={operation}: {e}")


def get_summary() -> Dict[str, Any]:
    """Returns {operation: {sample_count, avg/p50/p95/max_duration_ms}} -
    read at /metrics scrape time."""
    try:
        client = _client()
        operations = client.smembers(_OPERATIONS_KEY) or set()
    except Exception as e:
        logger.warning(f"Failed to read latency tracking operations: {e}")
        operations = set()

    return {operation: _read_operation(operation) for operation in sorted(operations)}


def _read_operation(operation: str) -> Dict[str, Any]:
    try:
        client = _client()
        raw_samples = client.lrange(f"{_KEY_PREFIX}:{operation}:samples", 0, -1) or []
        samples = sorted(float(s) for s in raw_samples)
    except Exception as e:
        logger.warning(f"Failed to read latency samples for operation={operation}: {e}")
        samples = []

    if not samples:
        return dict(_EMPTY_STATS)

    def _percentile(p: float) -> float:
        index = min(int(len(samples) * p), len(samples) - 1)
        return samples[index]

    return {
        "sample_count": len(samples),
        "avg_duration_ms": round(sum(samples) / len(samples), 3),
        "p50_duration_ms": round(_percentile(0.50), 3),
        "p95_duration_ms": round(_percentile(0.95), 3),
        "max_duration_ms": round(samples[-1], 3),
    }


def track_latency(operation: str) -> Callable:
    """Decorator recording real wall-clock duration to the Redis-backed
    tracker above - the cross-process-visible counterpart to
    performance_monitor.track_performance, for callers that specifically
    need p50/p95 queryable regardless of which container ran them."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                record_duration(operation, (time.time() - start) * 1000)
        return wrapper
    return decorator
