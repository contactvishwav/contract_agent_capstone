"""
Prometheus-compatible metrics (production-readiness audit finding #10),
served at GET /metrics.

Deliberately infra-agnostic - no deployment target needed, just standard
`prometheus_client` collectors any Prometheus-compatible scraper can pull
from directly. Three families:

- HTTP request counts/latency by route, recorded per-request by
  PrometheusMiddleware (backend/shared/middleware/metrics.py).
- LLM cost/token counters (audit finding #1's Redis-backed
  llm_usage_tracker) - read fresh from Redis and copied onto Gauges at
  scrape time, since the real source of truth already lives there
  (cross-process, see that module's own docstring), not duplicated as a
  second in-process counter that could drift from it.
- Celery task-state counts (celery_task_metrics.py) - same read-at-scrape-
  time pattern, for the same cross-process reason (the worker container
  is what runs tasks; this process is what Prometheus scrapes).
"""

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest

from backend.shared.monitoring.celery_task_metrics import get_task_state_counts
from backend.shared.monitoring.llm_usage_tracker import llm_usage_tracker

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


def render_metrics() -> bytes:
    """Refresh the read-at-scrape-time gauges, then render every
    registered collector in the default registry as Prometheus text
    exposition format."""
    _refresh_llm_usage_gauges()
    _refresh_celery_task_gauges()
    return generate_latest(REGISTRY)
