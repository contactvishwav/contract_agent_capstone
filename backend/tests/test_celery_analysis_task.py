"""
Celery task tests (closes the README's "Async Batch Workflows" future-
enhancement item; backend/tasks.py has the full scoping rationale for why
contract analysis specifically was moved here).

CELERY_TASK_ALWAYS_EAGER=true (set in backend/tests/conftest.py, before
backend.celery_app is ever imported) means .delay(...) calls in this file
execute the real task function synchronously, in-process - a genuine
Celery execution path (EagerResult supports the same .state/.result/.get()
interface as a real AsyncResult), not a hand-rolled substitute, without
needing a broker or a running worker.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.tasks import analyze_contract_task
    from backend.celery_app import celery_app

from backend.tests.conftest import auth_headers


def _fake_intelligence(processing_complete=True, node_status=None, risk_level="LOW"):
    return SimpleNamespace(
        processing_complete=processing_complete,
        node_status=node_status or {"extract_clauses": "success", "check_policies": "success"},
        processing_time=1.23,
        clauses=[],
        violations=[],
        risk_assessment=SimpleNamespace(
            overall_risk_score=10.0, risk_level=risk_level,
            critical_issues=[], critical_issue_details=[], recommendations=[],
        ),
        redlines=[],
        cuad_deviations=[], jurisdiction_info={}, precedent_matches=[],
        # Supervisor rebuild fields - see domain/entities.py's
        # ContractIntelligence and tasks.py's _intelligence_to_response_dict.
        quality_grade={}, escalated=False, analysis_method=None,
        execution_path="plan_execution_engine", planned_execution=True,
    )


class CeleryConfigTests(unittest.TestCase):
    def test_eager_mode_is_active_for_tests(self):
        """If this is False, every other test in this file is silently
        testing something other than what it claims to."""
        self.assertTrue(celery_app.conf.task_always_eager)


class TaskProcessesSuccessfullyTests(unittest.TestCase):
    def test_real_worker_eager_execution_returns_expected_shape(self):
        """A real (eager) worker processing the task - not a mock of the
        task itself - produces the same response shape the synchronous
        /analyze route used to return directly."""
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(return_value=_fake_intelligence())

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            result = analyze_contract_task.delay("CONTRACT_1", "tenant_a", "gemini-2.5-flash", True)

        self.assertEqual(result.state, "SUCCESS")
        payload = result.result
        self.assertEqual(payload["contract_id"], "CONTRACT_1")
        self.assertTrue(payload["analysis_complete"])
        self.assertEqual(payload["model_used"], "gemini-2.5-flash")
        self.assertEqual(payload["execution_path"], "plan_execution_engine")
        self.assertTrue(payload["planned_execution"])
        self.assertIn("results", payload)
        self.assertIn("risk_assessment", payload["results"])

        # The service was actually called with this tenant's id, not a
        # default or a different one.
        fake_service.analyze_contract_by_id.assert_awaited_once_with("CONTRACT_1", "tenant_a", "gemini-2.5-flash", True)

    def test_partial_failure_analysis_still_reports_honestly_through_the_task(self):
        """A task that completes (Celery SUCCESS) can still carry an
        analysis that itself reports processing_complete=False - Celery's
        own success/failure state must not collapse this distinction
        (P1's honest partial-failure reporting has to survive being
        wrapped in a task)."""
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(
            return_value=_fake_intelligence(processing_complete=False, node_status={"check_policies": "partial"})
        )

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            result = analyze_contract_task.delay("CONTRACT_1", "tenant_a")

        self.assertEqual(result.state, "SUCCESS")  # the task itself didn't crash
        self.assertFalse(result.result["analysis_complete"])  # but the analysis was honest about being partial
        self.assertEqual(result.result["node_status"]["check_policies"], "partial")


class TaskFailureSurfacesHonestlyTests(unittest.TestCase):
    def test_contract_not_found_raises_task_failure_not_a_silent_result(self):
        """Was raise HTTPException(404) in the old synchronous route -
        equivalent honesty in the task world is a real Celery FAILURE
        state, not a result dict pretending everything is fine."""
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(return_value=None)

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            result = analyze_contract_task.apply(args=("MISSING_CONTRACT", "tenant_a"))

        self.assertEqual(result.state, "FAILURE")
        self.assertIn("not found", str(result.result).lower())

    def test_unexpected_exception_is_not_swallowed(self):
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(side_effect=RuntimeError("simulated pipeline crash"))

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            result = analyze_contract_task.apply(args=("CONTRACT_1", "tenant_a"))

        self.assertEqual(result.state, "FAILURE")
        self.assertIn("simulated pipeline crash", str(result.result))


class AnalyzeRouteEnqueuesAndStatusPollingTests(unittest.TestCase):
    """The route layer: POST /analyze enqueues and returns immediately;
    GET /tasks/{task_id}/status reflects real Celery state transitions."""

    def setUp(self):
        self.client = TestClient(app)

    def test_analyze_route_enqueues_and_returns_202_with_task_id(self):
        with patch("backend.api.contract_intelligence.task_ownership_store.enqueue") as mock_enqueue:
            mock_enqueue.return_value = MagicMock(id="task-abc-123")
            response = self.client.post(
                "/api/intelligence/contracts/CONTRACT_1/analyze",
                headers=auth_headers(tenant_id="tenant_a", role="ADMIN"),
            )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["task_id"], "task-abc-123")
        self.assertEqual(body["status"], "PENDING")
        self.assertIn("task-abc-123", body["status_url"])
        mock_enqueue.assert_called_once()

    def test_status_polling_reflects_success_after_real_eager_execution(self):
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(return_value=_fake_intelligence())

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            task = analyze_contract_task.delay("CONTRACT_1", "tenant_a")

        with patch("backend.api.contract_intelligence.task_ownership_store.is_owner", return_value=True):
            response = self.client.get(
                f"/api/intelligence/tasks/{task.id}/status",
                headers=auth_headers(tenant_id="tenant_a", role="ADMIN"),
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "SUCCESS")
        self.assertEqual(body["result"]["contract_id"], "CONTRACT_1")

    def test_status_polling_reflects_failure_after_real_eager_execution(self):
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(return_value=None)

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            task = analyze_contract_task.apply(args=("MISSING_CONTRACT", "tenant_a"))

        with patch("backend.api.contract_intelligence.task_ownership_store.is_owner", return_value=True):
            response = self.client.get(
                f"/api/intelligence/tasks/{task.id}/status",
                headers=auth_headers(tenant_id="tenant_a", role="ADMIN"),
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "FAILURE")
        self.assertIn("not found", body["error"].lower())

    def test_status_polling_for_unknown_task_id_is_not_found(self):
        with patch("backend.api.contract_intelligence.task_ownership_store.is_owner", return_value=False):
            response = self.client.get(
                "/api/intelligence/tasks/never-enqueued-task-id/status",
                headers=auth_headers(tenant_id="tenant_a", role="ADMIN"),
            )
        self.assertEqual(response.status_code, 404)

    def test_status_polling_requires_auth(self):
        response = self.client.get("/api/intelligence/tasks/some-task-id/status")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
