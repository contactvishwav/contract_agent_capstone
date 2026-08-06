"""
Real-time per-step progress for a running analysis, via Redis pub/sub -
genuine publisher/subscriber decoupling, not a disguised direct call.
PlanExecutionEngine.execute_plan publishes one message per step
transition without knowing or caring whether anything is listening;
GET /api/supervisor/workflow/{contract_id}/stream (the SSE route) - or
any other future subscriber, including multiple concurrent ones watching
the same run - subscribes without the engine knowing they exist.

Real gap this closes: today the only visibility into a running analysis
is polling coarse Celery state (PENDING/STARTED/SUCCESS/FAILURE) - no
per-step progress, despite a real analysis genuinely taking 30s-2min
(observed live). This gives a client something to actually watch.

Design trade-offs, stated plainly rather than hidden:
- The channel is keyed by contract_id, not a per-run task_id. Threading
  a Celery task_id all the way from tasks.py's bound task down through
  analyze_contract_by_id -> analyze_contract_intelligence ->
  orchestrator.analyze_contract -> _analyze_with_planning -> execute_plan
  (6 layers) for a channel key alone was judged not worth the plumbing
  when contract_id is already available at every one of those layers and
  is exactly what the client already has before analysis even starts. The
  real cost: two concurrent re-analyses of the same contract_id would
  interleave their progress messages on one channel - a genuine but rare
  edge case for a "watch the run you just triggered" feature, not a
  correctness issue for node_status/the audit trail/the final result,
  which are all still per-run-correct regardless.
- Fire-and-forget: a message published with no active subscriber is
  lost, exactly like any pub/sub channel. This is for watching a live
  run, not a durable record - node_status and the audit trail already
  provide that after the fact.
- Requires real Redis. The InMemoryCache fallback used when Redis is
  unreachable (backend/shared/cache/redis_cache.py) has no real pub/sub;
  publish()/pubsub() there are safe no-ops rather than raising, matching
  every other Redis-backed feature's degraded-fallback posture in this
  codebase - live progress just isn't available in that mode.
"""

import json
import time
from typing import Any, Optional

from backend.shared.cache.redis_cache import cache
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

_CHANNEL_PREFIX = "workflow_progress"


def channel_name(contract_id: str) -> str:
    return f"{_CHANNEL_PREFIX}:{contract_id}"


def publish_step_progress(contract_id: Optional[str], step_type: str, status: str, **extra: Any) -> None:
    """Never raises - a progress-publish failure must never break a real
    analysis run. A no-op if contract_id is unknown (nothing to key the
    channel on)."""
    if not contract_id:
        return
    try:
        message = json.dumps({
            "step_type": step_type,
            "status": status,
            "timestamp": time.time(),
            **extra,
        })
        cache.redis_client.publish(channel_name(contract_id), message)
    except Exception as e:
        logger.warning(f"Failed to publish workflow progress for contract {contract_id}: {e}")


def subscribe(contract_id: str):
    """Returns a redis-py PubSub object already subscribed to this
    contract's progress channel (or a no-op stand-in under the
    InMemoryCache fallback - see module docstring)."""
    pubsub = cache.redis_client.pubsub()
    pubsub.subscribe(channel_name(contract_id))
    return pubsub
