"""
Redis-backed Celery task-state counters (production-readiness audit
finding #10, feeding /api/monitoring/metrics).

Same cross-process problem as llm_usage_tracker.py, and the same fix:
Celery tasks actually run in the `worker` container (backend/tasks.py),
but Prometheus scrapes /api/monitoring/metrics on the `backend`
container - an in-worker-process Prometheus Counter would never be seen
by a scrape hitting the other process. Counters live in the same Redis
instance already deployed for caching/broker duties instead, keyed by
task name + terminal state, and read back at scrape time.

record_task_state() is wired up as a Celery signal handler
(backend/celery_app.py's task_prerun/task_success/task_failure/
task_retry) - never raises, matching llm_usage_tracker's fail-open
discipline (a metrics bug must never break real task processing).
"""

from typing import Dict

from backend.shared.cache.redis_cache import cache
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "celery_task_metrics"
_STATES_KEY = f"{_KEY_PREFIX}:states"


def _client():
    return cache.redis_client


def record_task_state(task_name: str, state: str) -> None:
    """state is one of "started"/"success"/"failure"/"retry"."""
    try:
        client = _client()
        pair = f"{task_name}:{state}"
        client.sadd(_STATES_KEY, pair)
        client.incr(f"{_KEY_PREFIX}:count:{pair}")
    except Exception as e:
        logger.warning(f"Failed to record Celery task metric ({task_name}/{state}): {e}")


def get_task_state_counts() -> Dict[str, Dict[str, int]]:
    """Returns {task_name: {state: count, ...}, ...} - read at
    /api/monitoring/metrics scrape time."""
    try:
        client = _client()
        pairs = client.smembers(_STATES_KEY) or set()
    except Exception as e:
        logger.warning(f"Failed to read Celery task metric keys: {e}")
        return {}

    counts: Dict[str, Dict[str, int]] = {}
    for pair in sorted(pairs):
        task_name, _, state = pair.rpartition(":")
        if not task_name:
            continue
        try:
            raw = _client().get(f"{_KEY_PREFIX}:count:{pair}")
            value = int(raw) if raw is not None else 0
        except Exception:
            value = 0
        counts.setdefault(task_name, {})[state] = value

    return counts
