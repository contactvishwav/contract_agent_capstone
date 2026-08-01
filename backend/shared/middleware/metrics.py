import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.shared.monitoring.prometheus_metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Records request count/latency for every request, labeled by the
    matched route *template* (e.g. "/api/documents/{contract_id}"), not
    the raw URL - using the raw path would let every distinct contract_id
    ever requested mint its own Prometheus time series (unbounded
    cardinality). The template is only known once FastAPI's router has
    matched the request, which happens inside `call_next` - read back from
    `request.scope["route"]` afterwards, same technique
    prometheus-fastapi-instrumentator uses. Falls back to the raw path for
    truly unmatched requests (404s), which is a small, bounded set in
    practice (dropped a call, not fabricated a template that doesn't
    exist).
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        route = request.scope.get("route")
        path = route.path if route is not None else request.url.path

        HTTP_REQUESTS_TOTAL.labels(method=request.method, path=path, status=response.status_code).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, path=path).observe(duration)

        return response
