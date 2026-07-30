"""
Basic LLM cost/usage monitoring (P3 item 20). Tracks running totals of
token usage and estimated cost per operation, in-memory only - matches
performance_monitor.py's scope (not persisted, lost on restart; a
lightweight running counter, not a billing-grade observability platform).

Pricing is an approximate, override-able estimate (list price per million
tokens), not wired to real billing - enough to compare relative cost across
operations and catch an obviously expensive regression, which is what
"basic monitoring" needs here.
"""

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

# Approximate Gemini 2.5 Flash list pricing (USD per 1M tokens as of this
# writing). Override via env vars if the model/billing tier changes - this
# is an estimate for relative cost visibility, not a billing-grade figure.
INPUT_PRICE_PER_1M = float(os.getenv("LLM_INPUT_PRICE_PER_1M_TOKENS", "0.30"))
OUTPUT_PRICE_PER_1M = float(os.getenv("LLM_OUTPUT_PRICE_PER_1M_TOKENS", "2.50"))

# Bounded per-operation history, matching performance_monitor.py's
# last-N-per-operation convention.
MAX_EVENTS_PER_OPERATION = 500


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * INPUT_PRICE_PER_1M + (output_tokens / 1_000_000) * OUTPUT_PRICE_PER_1M


@dataclass
class LLMUsageEvent:
    operation: str
    model: str
    cache_hit: bool
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


class LLMUsageTracker:
    """Running totals of LLM token usage/cost/cache-hit rate, per operation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events: Dict[str, list] = {}

    def record_call(
        self,
        operation: str,
        model: str,
        cache_hit: bool,
        usage_metadata: Optional[Dict[str, Any]] = None,
    ) -> LLMUsageEvent:
        usage_metadata = usage_metadata or {}
        input_tokens = int(usage_metadata.get("input_tokens", 0) or 0)
        output_tokens = int(usage_metadata.get("output_tokens", 0) or 0)
        # A cache hit made no LLM call, so it cost nothing - regardless of
        # what the original (now-reused) call's token counts were.
        cost = 0.0 if cache_hit else estimate_cost_usd(input_tokens, output_tokens)

        event = LLMUsageEvent(
            operation=operation,
            model=model,
            cache_hit=cache_hit,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
        )

        with self._lock:
            events = self._events.setdefault(operation, [])
            events.append(event)
            if len(events) > MAX_EVENTS_PER_OPERATION:
                del events[:-MAX_EVENTS_PER_OPERATION]

        logger.info(
            f"LLM call: operation={operation} model={model} cache_hit={cache_hit} "
            f"input_tokens={input_tokens} output_tokens={output_tokens} "
            f"estimated_cost_usd={cost:.6f}"
        )
        return event

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = {op: list(events) for op, events in self._events.items()}

        all_events = [e for events in snapshot.values() for e in events]
        return {
            "overall": self._summarize(all_events),
            "by_operation": {op: self._summarize(events) for op, events in snapshot.items()},
        }

    @staticmethod
    def _summarize(events: list) -> Dict[str, Any]:
        total_calls = len(events)
        cache_hits = sum(1 for e in events if e.cache_hit)
        return {
            "total_calls": total_calls,
            "cache_hits": cache_hits,
            "cache_hit_rate": (cache_hits / total_calls) if total_calls else 0.0,
            "total_input_tokens": sum(e.input_tokens for e in events),
            "total_output_tokens": sum(e.output_tokens for e in events),
            "total_estimated_cost_usd": round(sum(e.estimated_cost_usd for e in events), 6),
        }


# Global tracker instance, matching performance_monitor.py's module-level
# singleton convention.
llm_usage_tracker = LLMUsageTracker()
