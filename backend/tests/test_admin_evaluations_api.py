"""Phase 5 (MLOps governance harness): GET /api/admin/evaluations is gated by
identity (requires_role(ADMIN)), same reasoning as Phase 4's human-review
endpoints - a system-wide quality metric, not tenant-owned data, so there is
no tenant predicate to test, only the role gate and the two response shapes
(no results artifact yet vs. a real one)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph") as _MockNeo4jGraph, \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    _MockNeo4jGraph.return_value.query.return_value = []
    from backend.main import app
    from backend.governance.rbac import UserRole
    import backend.api.admin_evaluations_api as admin_evaluations_api

from backend.tests.conftest import auth_headers


class TestAdminEvaluationsAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_missing_token_is_401(self):
        response = self.client.get("/api/admin/evaluations")
        self.assertEqual(response.status_code, 401)

    def test_non_admin_role_is_403(self):
        response = self.client.get(
            "/api/admin/evaluations",
            headers=auth_headers(role=UserRole.LEGAL_REVIEWER.value),
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_with_no_results_file_reports_unavailable(self):
        with patch.object(admin_evaluations_api, "RESULTS_PATH", Path("/nonexistent/latest_results.json")):
            response = self.client.get(
                "/api/admin/evaluations",
                headers=auth_headers(role=UserRole.ADMIN.value),
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["available"])

    def test_admin_with_results_file_returns_metrics(self):
        fake_results = {
            "generated_at": "2026-08-15T00:00:00+00:00",
            "k": 3,
            "search_level": "document",
            "query_count": 10,
            "aggregate": {"mean_recall_at_k": 0.9, "mean_ndcg_at_k": 0.85},
            "per_query": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            results_path = Path(tmp_dir) / "latest_results.json"
            results_path.write_text(json.dumps(fake_results))
            with patch.object(admin_evaluations_api, "RESULTS_PATH", results_path):
                response = self.client.get(
                    "/api/admin/evaluations",
                    headers=auth_headers(role=UserRole.ADMIN.value),
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["aggregate"]["mean_recall_at_k"], 0.9)
        self.assertEqual(body["aggregate"]["mean_ndcg_at_k"], 0.85)
        self.assertEqual(body["query_count"], 10)


if __name__ == "__main__":
    unittest.main()
