"""
LLM cost/usage monitoring (P3 item 20), backed by Redis-shared counters.

Was in-process only (a plain dict) - fine when everything ran inside one
FastAPI process, but the Celery migration (backend/tasks.py) moved the
real analysis pipeline - and with it, every LLMExtractionService/
PolicyEvaluationService call this tracker records - into the separate
`worker` container. GET /api/monitoring/llm-usage is served by the
`backend` container. Two separate OS processes, two separate copies of
Python's memory: an in-process tracker in `backend` was blind to almost
all real spend, since the calls that actually cost money now happen in
`worker`.

Counters now live in the same Redis instance already deployed for caching
(shared/cache/redis_cache.py's `cache` singleton, `REDIS_URL`) - both
processes read/write the same keys, so the dashboard reflects real spend
regardless of which container made the call. Falls back to the same
process-local InMemoryCache every other cache in this codebase falls back
to when no real Redis is reachable (e.g. local dev without Redis, or
tests) - correctness holds either way; only cross-process visibility is
lost in that fallback case, same tradeoff already accepted everywhere
else caching is used here.

Pricing is an approximate, override-able estimate (list price per million
tokens), not wired to real billing - enough to compare relative cost
across operations and catch an obviously expensive regression.
"""

import os
from typing import Any, Dict, Optional

from backend.shared.cache.redis_cache import cache
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

# Approximate Gemini 2.5 Flash list pricing (USD per 1M tokens as of this
# writing). Override via env vars if the model/billing tier changes - this
# is an estimate for relative cost visibility, not a billing-grade figure.
INPUT_PRICE_PER_1M = float(os.getenv("LLM_INPUT_PRICE_PER_1M_TOKENS", "0.30"))
OUTPUT_PRICE_PER_1M = float(os.getenv("LLM_OUTPUT_PRICE_PER_1M_TOKENS", "2.50"))

_KEY_PREFIX = "llm_usage"
_OPERATIONS_KEY = f"{_KEY_PREFIX}:operations"

# Provider dimension (added alongside real multi-provider fallback -
# backend/agents/llm_fallback_service.py): every real call recorded before
# that work was, unconditionally, Gemini - there was no other provider
# configured. `model` was always recorded as a string but never broken out
# as its own queryable dimension, so there was no way to tell "the
# extraction benchmark's numbers reflect Gemini only" from "these numbers
# are a blend of providers" after fallback exists. Inferred from `model`
# rather than requiring every call site to pass a redundant explicit
# provider string.
_PROVIDER_MARKERS = (
    ("gemini", "gemini"),
    ("gpt", "openai"),
    ("claude", "anthropic"),
)


def _infer_provider(model: str) -> str:
    model_lower = (model or "").lower()
    for marker, provider in _PROVIDER_MARKERS:
        if marker in model_lower:
            return provider
    return "unknown"


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * INPUT_PRICE_PER_1M + (output_tokens / 1_000_000) * OUTPUT_PRICE_PER_1M


class LLMUsageTracker:
    """Running totals of LLM token usage/cost/cache-hit rate, per
    operation - stored as Redis counters shared across every process
    that imports this module, not a single process's memory."""

    def __init__(self, redis_client=None):
        # Stored, not resolved once: `cache.redis_client` can be
        # reassigned after this module is imported (RedisCache._connect
        # itself falls back at runtime; tests patch `cache.redis_client`
        # directly), so look it up fresh on every call unless a specific
        # client was injected (tests that want real isolation from the
        # global singleton).
        self._explicit_client = redis_client

    @property
    def _client(self):
        return self._explicit_client if self._explicit_client is not None else cache.redis_client

    def record_call(
        self,
        operation: str,
        model: str,
        cache_hit: bool,
        usage_metadata: Optional[Dict[str, Any]] = None,
        is_fallback: bool = False,
    ) -> None:
        """
        is_fallback: True when this call was actually served by a
        secondary provider (backend/agents/llm_fallback_service.py),
        i.e. the primary provider (Gemini) was unavailable for this
        specific request. False (the default) covers both "no fallback
        infrastructure involved" and "fallback infrastructure involved,
        but the primary provider served it anyway" - both are the normal
        case today. Provider itself is inferred from `model` (see
        _infer_provider) rather than requiring a second, redundant param.
        """
        provider = _infer_provider(model)
        usage_metadata = usage_metadata or {}
        input_tokens = int(usage_metadata.get("input_tokens", 0) or 0)
        output_tokens = int(usage_metadata.get("output_tokens", 0) or 0)
        # A cache hit made no LLM call, so it cost nothing - regardless of
        # what the original (now-reused) call's token counts were.
        cost = 0.0 if cache_hit else estimate_cost_usd(input_tokens, output_tokens)

        try:
            client = self._client
            client.sadd(_OPERATIONS_KEY, operation)
            client.incr(f"{_KEY_PREFIX}:{operation}:total_calls")
            if cache_hit:
                client.incr(f"{_KEY_PREFIX}:{operation}:cache_hits")
            if input_tokens:
                client.incrby(f"{_KEY_PREFIX}:{operation}:total_input_tokens", input_tokens)
            if output_tokens:
                client.incrby(f"{_KEY_PREFIX}:{operation}:total_output_tokens", output_tokens)
            if cost:
                client.incrbyfloat(f"{_KEY_PREFIX}:{operation}:total_estimated_cost_usd", cost)

            # Provider breakdown - additive, doesn't change any of the
            # operation-level counters above.
            client.sadd(f"{_KEY_PREFIX}:{operation}:providers", provider)
            client.incr(f"{_KEY_PREFIX}:{operation}:by_provider:{provider}:total_calls")
            if is_fallback:
                client.incr(f"{_KEY_PREFIX}:{operation}:fallback_calls")
        except Exception as e:
            # Usage tracking must never break the actual LLM call it's
            # observing - log and move on, matching RedisCache.get/set's
            # own failure-handling convention.
            logger.error(f"Failed to record LLM usage for operation={operation}: {e}")
            return

        logger.info(
            f"LLM call: operation={operation} model={model} provider={provider} "
            f"is_fallback={is_fallback} cache_hit={cache_hit} "
            f"input_tokens={input_tokens} output_tokens={output_tokens} "
            f"estimated_cost_usd={cost:.6f}"
        )

    def get_summary(self) -> Dict[str, Any]:
        client = self._client
        try:
            operations = client.smembers(_OPERATIONS_KEY) or set()
        except Exception as e:
            logger.error(f"Failed to read LLM usage operations: {e}")
            operations = set()

        by_operation = {op: self._read_operation(client, op) for op in sorted(operations)}
        return {"overall": self._sum_all(by_operation.values()), "by_operation": by_operation}

    def _read_operation(self, client, operation: str) -> Dict[str, Any]:
        def _int(field: str) -> int:
            try:
                value = client.get(f"{_KEY_PREFIX}:{operation}:{field}")
                return int(float(value)) if value is not None else 0
            except Exception:
                return 0

        def _float(field: str) -> float:
            try:
                value = client.get(f"{_KEY_PREFIX}:{operation}:{field}")
                return float(value) if value is not None else 0.0
            except Exception:
                return 0.0

        total_calls = _int("total_calls")
        cache_hits = _int("cache_hits")

        try:
            providers = client.smembers(f"{_KEY_PREFIX}:{operation}:providers") or set()
        except Exception:
            providers = set()
        by_provider = {
            p: _int(f"by_provider:{p}:total_calls") for p in sorted(providers)
        }
        fallback_calls = _int("fallback_calls")

        return {
            "total_calls": total_calls,
            "cache_hits": cache_hits,
            "cache_hit_rate": (cache_hits / total_calls) if total_calls else 0.0,
            "total_input_tokens": _int("total_input_tokens"),
            "total_output_tokens": _int("total_output_tokens"),
            "total_estimated_cost_usd": round(_float("total_estimated_cost_usd"), 6),
            # Provider visibility (real multi-provider fallback): which
            # provider(s) actually served this operation's real calls, and
            # how many of those were served by a fallback provider rather
            # than the primary. by_provider having exactly one key (today,
            # always "gemini") means these numbers reflect ONE provider's
            # behavior only, not a blended scenario - important context for
            # anyone reading benchmark numbers against this operation.
            "by_provider": by_provider,
            "fallback_calls": fallback_calls,
            "is_single_provider": len(by_provider) <= 1,
        }

    @staticmethod
    def _sum_all(op_summaries) -> Dict[str, Any]:
        op_summaries = list(op_summaries)
        total_calls = sum(s["total_calls"] for s in op_summaries)
        cache_hits = sum(s["cache_hits"] for s in op_summaries)

        by_provider: Dict[str, int] = {}
        for s in op_summaries:
            for provider, count in s.get("by_provider", {}).items():
                by_provider[provider] = by_provider.get(provider, 0) + count

        return {
            "total_calls": total_calls,
            "cache_hits": cache_hits,
            "cache_hit_rate": (cache_hits / total_calls) if total_calls else 0.0,
            "total_input_tokens": sum(s["total_input_tokens"] for s in op_summaries),
            "total_output_tokens": sum(s["total_output_tokens"] for s in op_summaries),
            "total_estimated_cost_usd": round(sum(s["total_estimated_cost_usd"] for s in op_summaries), 6),
            "by_provider": by_provider,
            "fallback_calls": sum(s.get("fallback_calls", 0) for s in op_summaries),
            "is_single_provider": len(by_provider) <= 1,
        }


# Global tracker instance, matching performance_monitor.py's module-level
# singleton convention. Safe to share across processes now - state lives
# in Redis, not on this object.
llm_usage_tracker = LLMUsageTracker()
