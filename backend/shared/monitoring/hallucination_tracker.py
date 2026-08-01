"""
Redis-backed hallucination/grounding-rate tracking (AI-engineering-depth
audit finding #12). Same cross-process reasoning as findings #1/#10:
extraction and policy evaluation run in the Celery `worker` container,
while GET /api/monitoring/llm-usage and GET /metrics are served by the
`backend` container - counters live in the same shared Redis instance
already deployed for caching, not in either process's own memory.

Two independently tracked categories, both previously log-only (a line
nobody was watching, with no persisted rate):

- "clause_extraction": an LLM-extracted clause is "ungrounded" when its
  verbatim text can't be located anywhere in the source contract
  (LLMExtractionService._find_span returning its -1,-1 sentinel) - the
  model may have paraphrased or hallucinated it rather than quoting it.
- "policy_citation": a policy violation is discarded when its cited
  rule_id wasn't actually among the rules offered to the model
  (PolicyEvaluationService.evaluate_clause) - the model cited a policy
  that doesn't exist.
"""

from typing import Any, Dict

from backend.shared.cache.redis_cache import cache
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "hallucination_tracking"
_CATEGORIES_KEY = f"{_KEY_PREFIX}:categories"


def _client():
    return cache.redis_client


def record(category: str, total: int, flagged: int) -> None:
    """total is the number of items checked (extracted clauses, or LLM-
    returned citations); flagged is how many were ungrounded/discarded.
    A no-op if total is 0 - nothing was checked, so nothing to record.
    Never raises: a tracking bug must never break real extraction/
    evaluation, matching llm_usage_tracker's own failure-handling
    convention."""
    if total <= 0:
        return
    try:
        client = _client()
        client.sadd(_CATEGORIES_KEY, category)
        client.incrby(f"{_KEY_PREFIX}:{category}:total", total)
        if flagged:
            client.incrby(f"{_KEY_PREFIX}:{category}:flagged", flagged)
    except Exception as e:
        logger.warning(f"Failed to record hallucination tracking for category={category}: {e}")


def get_summary() -> Dict[str, Any]:
    """Returns {"overall": {...}, "by_category": {category: {...}}} - read
    at /api/monitoring/llm-usage and /metrics scrape time."""
    try:
        client = _client()
        categories = client.smembers(_CATEGORIES_KEY) or set()
    except Exception as e:
        logger.warning(f"Failed to read hallucination tracking categories: {e}")
        categories = set()

    by_category = {category: _read_category(category) for category in sorted(categories)}

    total = sum(c["total"] for c in by_category.values())
    flagged = sum(c["flagged"] for c in by_category.values())
    return {
        "overall": {"total": total, "flagged": flagged, "rate": round((flagged / total) if total else 0.0, 6)},
        "by_category": by_category,
    }


def _read_category(category: str) -> Dict[str, Any]:
    client = _client()

    def _int(field: str) -> int:
        try:
            value = client.get(f"{_KEY_PREFIX}:{category}:{field}")
            return int(value) if value is not None else 0
        except Exception:
            return 0

    total = _int("total")
    flagged = _int("flagged")
    return {"total": total, "flagged": flagged, "rate": round((flagged / total) if total else 0.0, 6)}
