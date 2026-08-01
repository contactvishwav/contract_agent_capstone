"""
Prometheus-compatible metrics (production-readiness audit finding #10),
served at GET /metrics.

Deliberately infra-agnostic - no deployment target needed, just standard
`prometheus_client` collectors any Prometheus-compatible scraper can pull
from directly. Three families:

- HTTP request counts/latency by route, recorded per-request by
  PrometheusMiddleware (backend/shared/middleware/metrics.py) - a native
  Prometheus Histogram, since every sample is observed directly in this
  process (a request is always handled and scraped by `backend`).
- LLM cost/token counters (audit finding #1's Redis-backed
  llm_usage_tracker), hallucination/grounding rates (finding #12's
  hallucination_tracker), and clause_extraction/policy_evaluation p50/p95
  latency (finding #13's latency_tracker) - all three read fresh from
  Redis and copied onto Gauges at scrape time, since the real source of
  truth already lives there (cross-process: these calls run in the
  `worker` container, not `backend`, so a native in-process Histogram
  would only ever see whichever container happened to serve the scrape).
- Celery task-state counts (celery_task_metrics.py) - same read-at-scrape-
  time pattern, for the same cross-process reason (the worker container
  is what runs tasks; this process is what Prometheus scrapes).
"""

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest

from backend.shared.monitoring.celery_task_metrics import get_task_state_counts
from backend.shared.monitoring.llm_usage_tracker import llm_usage_tracker
from backend.shared.monitoring import hallucination_tracker
from backend.shared.monitoring import latency_tracker

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)

# Cross-process p50/p95 for the primary analysis path (audit finding #13,
# closed further per follow-up) - sit alongside the HTTP latency histogram
# above, but as Gauges (read-at-scrape-time from Redis) rather than a
# native Histogram, since samples are observed in the `worker` container,
# not this one.
OPERATION_LATENCY_P50_MS = Gauge(
    "operation_latency_p50_ms",
    "p50 (median) duration in milliseconds, by operation",
    ["operation"],
)
OPERATION_LATENCY_P95_MS = Gauge(
    "operation_latency_p95_ms",
    "p95 duration in milliseconds, by operation",
    ["operation"],
)
OPERATION_LATENCY_AVG_MS = Gauge(
    "operation_latency_avg_ms",
    "Average duration in milliseconds over the current sample window, by operation",
    ["operation"],
)
OPERATION_LATENCY_SAMPLE_COUNT = Gauge(
    "operation_latency_sample_count",
    "Number of latency samples currently held in the rolling window, by operation",
    ["operation"],
)

LLM_USAGE_TOTAL_CALLS = Gauge(
    "llm_usage_total_calls",
    "Total LLM calls recorded, by operation",
    ["operation"],
)
LLM_USAGE_CACHE_HITS = Gauge(
    "llm_usage_cache_hits_total",
    "Total LLM calls served from cache (zero cost), by operation",
    ["operation"],
)
LLM_USAGE_INPUT_TOKENS = Gauge(
    "llm_usage_input_tokens_total",
    "Total LLM input tokens, by operation",
    ["operation"],
)
LLM_USAGE_OUTPUT_TOKENS = Gauge(
    "llm_usage_output_tokens_total",
    "Total LLM output tokens, by operation",
    ["operation"],
)
LLM_USAGE_ESTIMATED_COST_USD = Gauge(
    "llm_usage_estimated_cost_usd_total",
    "Total estimated LLM cost in USD, by operation",
    ["operation"],
)

CELERY_TASK_STATE_COUNT = Gauge(
    "celery_task_state_count",
    "Celery task counts by task name and terminal state",
    ["task_name", "state"],
)

HALLUCINATION_RATE = Gauge(
    "hallucination_rate",
    "Fraction of checked items flagged as ungrounded/hallucinated, by category",
    ["category"],
)
HALLUCINATION_TOTAL = Gauge(
    "hallucination_checked_total",
    "Total items checked for grounding, by category",
    ["category"],
)
HALLUCINATION_FLAGGED = Gauge(
    "hallucination_flagged_total",
    "Total items flagged as ungrounded/hallucinated, by category",
    ["category"],
)


def _refresh_llm_usage_gauges() -> None:
    summary = llm_usage_tracker.get_summary()
    for operation, stats in summary.get("by_operation", {}).items():
        LLM_USAGE_TOTAL_CALLS.labels(operation=operation).set(stats["total_calls"])
        LLM_USAGE_CACHE_HITS.labels(operation=operation).set(stats["cache_hits"])
        LLM_USAGE_INPUT_TOKENS.labels(operation=operation).set(stats["total_input_tokens"])
        LLM_USAGE_OUTPUT_TOKENS.labels(operation=operation).set(stats["total_output_tokens"])
        LLM_USAGE_ESTIMATED_COST_USD.labels(operation=operation).set(stats["total_estimated_cost_usd"])


def _refresh_celery_task_gauges() -> None:
    for task_name, states in get_task_state_counts().items():
        for state, count in states.items():
            CELERY_TASK_STATE_COUNT.labels(task_name=task_name, state=state).set(count)


def _refresh_hallucination_gauges() -> None:
    for category, stats in hallucination_tracker.get_summary().get("by_category", {}).items():
        HALLUCINATION_RATE.labels(category=category).set(stats["rate"])
        HALLUCINATION_TOTAL.labels(category=category).set(stats["total"])
        HALLUCINATION_FLAGGED.labels(category=category).set(stats["flagged"])


def _refresh_operation_latency_gauges() -> None:
    for operation, stats in latency_tracker.get_summary().items():
        OPERATION_LATENCY_P50_MS.labels(operation=operation).set(stats["p50_duration_ms"])
        OPERATION_LATENCY_P95_MS.labels(operation=operation).set(stats["p95_duration_ms"])
        OPERATION_LATENCY_AVG_MS.labels(operation=operation).set(stats["avg_duration_ms"])
        OPERATION_LATENCY_SAMPLE_COUNT.labels(operation=operation).set(stats["sample_count"])


def render_metrics() -> bytes:
    """Refresh the read-at-scrape-time gauges, then render every
    registered collector in the default registry as Prometheus text
    exposition format."""
    _refresh_llm_usage_gauges()
    _refresh_celery_task_gauges()
    _refresh_hallucination_gauges()
    _refresh_operation_latency_gauges()
    return generate_latest(REGISTRY)
