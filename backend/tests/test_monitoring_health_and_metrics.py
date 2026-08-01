"""
Regression tests for reliability/observability audit finding #9:
GET /api/monitoring/health never checked Neo4j at all, and always
returned HTTP 200 regardless of what its "status" field said - a load
balancer or Docker HEALTHCHECK polling this route had no way to detect
an unhealthy backend from the response status alone.

Also covers finding #10 (GET /metrics) at a basic wiring level - the
Redis-backed counters themselves (findings #1/#10's Celery signals) are
covered by their own dedicated test files
(test_llm_usage_tracker_redis_backed.py, test_celery_task_metrics.py).
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.shared.utils import contract_search_tool
    from backend.shared.cache.redis_cache import cache, InMemoryCache
    import backend.api.monitoring_api as monitoring_api

from fastapi.testclient import TestClient


class HealthCheckReturnsRealStatusCodeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_healthy_when_neo4j_and_cache_both_reachable(self):
        with patch.object(monitoring_api, "neo4j_graph", MagicMock(query=MagicMock(return_value=[{"n": 1}]))), \
             patch.object(cache, "redis_client", MagicMock(ping=MagicMock(return_value=True))):
            response = self.client.get("/api/monitoring/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["components"]["neo4j"], "healthy")

    def test_neo4j_unreachable_returns_a_real_non_2xx_status(self):
        """The concrete before/after proof: simulate a real Neo4j failure
        (query raises, exactly what a dropped connection/auth failure/
        unreachable host looks like) and confirm the HTTP status itself -
        not just a body field - reflects it."""
        broken_graph = MagicMock()
        broken_graph.query.side_effect = Exception("Neo4j connection refused")

        with patch.object(monitoring_api, "neo4j_graph", broken_graph), \
             patch.object(cache, "redis_client", MagicMock(ping=MagicMock(return_value=True))):
            response = self.client.get("/api/monitoring/health")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["components"]["neo4j"], "unhealthy")
        self.assertEqual(body["components"]["cache"], "healthy")

    def test_cache_unreachable_also_returns_a_real_non_2xx_status(self):
        broken_cache_client = MagicMock()
        broken_cache_client.ping.side_effect = Exception("connection refused")

        with patch.object(monitoring_api, "neo4j_graph", MagicMock(query=MagicMock(return_value=[{"n": 1}]))), \
             patch.object(cache, "redis_client", broken_cache_client):
            response = self.client.get("/api/monitoring/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["components"]["cache"], "unhealthy")

    def test_health_check_itself_never_raises_even_on_total_failure(self):
        """Every dependency down at once - the route must still respond
        (with 503), not 500 from an unhandled exception inside the health
        check's own logic."""
        broken_graph = MagicMock()
        broken_graph.query.side_effect = Exception("neo4j down")
        broken_cache_client = MagicMock()
        broken_cache_client.ping.side_effect = Exception("redis down")

        with patch.object(monitoring_api, "neo4j_graph", broken_graph), \
             patch.object(cache, "redis_client", broken_cache_client):
            response = self.client.get("/api/monitoring/health")

        self.assertEqual(response.status_code, 503)


class MetricsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Isolation for the Redis-backed gauges scraped below - a fresh
        # backing store per test, same convention used throughout this
        # suite wherever a test touches Redis-backed tracker state
        # (e.g. test_hallucination_and_performance_tracking.py).
        cache.redis_client = InMemoryCache()

    def test_metrics_endpoint_is_reachable_without_auth(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])

    def test_metrics_include_http_request_counter_after_a_request(self):
        self.client.get("/")
        response = self.client.get("/metrics")
        self.assertIn('http_requests_total{method="GET",path="/",status="200"}', response.text)

    def test_metrics_use_route_template_not_raw_path_for_cardinality(self):
        """A path containing a real, arbitrary contract_id must be
        recorded under its route *template*, not the literal id - else
        every distinct id ever requested mints a new Prometheus series."""
        with patch.object(contract_search_tool, "graph", MagicMock(query=MagicMock(return_value=[{
            "has_document_embedding": False, "has_summary_embedding": False,
            "section_count": 0, "clause_count": 0,
            "relationship_count": 0, "relationship_embeddings": 0,
        }]))):
            self.client.get("/api/documents/enhanced/embedding-status/some-real-contract-id-abc123")

        response = self.client.get("/metrics")
        self.assertIn("/api/documents/enhanced/embedding-status/{contract_id}", response.text)
        self.assertNotIn("some-real-contract-id-abc123", response.text)

    def test_metrics_expose_llm_usage_and_celery_gauge_families(self):
        response = self.client.get("/metrics")
        for family in (
            "llm_usage_total_calls",
            "llm_usage_estimated_cost_usd_total",
            "celery_task_state_count",
        ):
            self.assertIn(family, response.text)

    def test_metrics_expose_operation_latency_gauge_family_alongside_http_histogram(self):
        """Finding #13 follow-up: the Redis-backed cross-process p50/p95
        for clause_extraction/policy_evaluation sits in /metrics alongside
        the existing (in-process, per-request) HTTP latency histogram."""
        from backend.shared.monitoring import latency_tracker

        latency_tracker.record_duration("clause_extraction", 250.0)

        response = self.client.get("/metrics")
        self.assertIn("http_request_duration_seconds", response.text)  # the existing histogram
        self.assertIn('operation_latency_p50_ms{operation="clause_extraction"} 250.0', response.text)
        self.assertIn('operation_latency_p95_ms{operation="clause_extraction"} 250.0', response.text)


if __name__ == "__main__":
    unittest.main()
