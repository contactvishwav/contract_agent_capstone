"""
Tests for Supervisor rebuild step 4: POST /api/supervisor/workflow/execute
and GET /api/supervisor/workflow/{id}/status, plus the plumbing that
carries quality_grade/escalated/analysis_method from PlanExecutionEngine's
result all the way to what a client polling status actually sees.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.api.supervisor_api as supervisor_api
    from backend.application.services.contract_intelligence_service import ContractIntelligenceService
    from backend.domain.entities import ContractIntelligence, RiskAssessment


class DomainEntityThreadingTests(unittest.TestCase):
    def test_convert_to_domain_entities_threads_quality_grade_escalated_analysis_method(self):
        service = ContractIntelligenceService.__new__(ContractIntelligenceService)
        analysis_result = {
            "clauses": [], "violations": [],
            "risk_assessment": {"overall_risk_score": 10.0, "risk_level": "LOW", "critical_issues": [], "recommendations": []},
            "redlines": [],
            "node_status": {"extract_clauses": "success"},
            "processing_complete": True,
            "quality_grade": {"grade": "A", "grounded_rate": 1.0},
            "escalated": False,
            "analysis_method": "optimized_phase3",
        }

        intelligence = service._convert_to_domain_entities(analysis_result)

        self.assertEqual(intelligence.quality_grade["grade"], "A")
        self.assertFalse(intelligence.escalated)
        self.assertEqual(intelligence.analysis_method, "optimized_phase3")

    def test_missing_keys_fall_back_to_safe_defaults_not_fabricated_values(self):
        # The traditional (non-planning) workflow path doesn't compute
        # these at all - must not fabricate a grade for it.
        service = ContractIntelligenceService.__new__(ContractIntelligenceService)
        analysis_result = {
            "clauses": [], "violations": [],
            "risk_assessment": {"overall_risk_score": 10.0, "risk_level": "LOW", "critical_issues": [], "recommendations": []},
            "redlines": [],
        }

        intelligence = service._convert_to_domain_entities(analysis_result)

        self.assertEqual(intelligence.quality_grade, {})
        self.assertFalse(intelligence.escalated)
        self.assertIsNone(intelligence.analysis_method)


class TaskResponseDictTests(unittest.TestCase):
    def test_intelligence_to_response_dict_includes_the_new_fields(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.tasks import _intelligence_to_response_dict

        intelligence = ContractIntelligence(
            clauses=[], violations=[],
            risk_assessment=RiskAssessment(overall_risk_score=10.0, risk_level="LOW", critical_issues=[], recommendations=[]),
            redlines=[],
        )
        intelligence.quality_grade = {"grade": "B"}
        intelligence.escalated = True
        intelligence.analysis_method = "fallback_phase1"

        response = _intelligence_to_response_dict("c1", "gemini-2.5-flash", intelligence)

        self.assertEqual(response["quality_grade"]["grade"], "B")
        self.assertTrue(response["escalated"])
        self.assertEqual(response["analysis_method"], "fallback_phase1")


class ExecuteWorkflowEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueues_the_real_analyze_task_with_planning_enabled(self):
        from backend.governance.auth import TokenIdentity
        identity = TokenIdentity(tenant_id="tenant_a", role="admin")
        request = supervisor_api.WorkflowExecuteRequest(contract_id="c1", model="gemini-2.5-flash")

        fake_task = MagicMock(id="task-123")
        fake_analyze_task = MagicMock()
        fake_analyze_task.apply_async.return_value = fake_task

        with patch("backend.tasks.analyze_contract_task", fake_analyze_task, create=True), \
             patch.object(supervisor_api.task_ownership_store, "enqueue", return_value=fake_task) as mock_enqueue:
            response = await supervisor_api.execute_workflow(request, identity=identity)

        mock_enqueue.assert_called_once_with(
            fake_analyze_task, "tenant_a", ("c1", "tenant_a", "gemini-2.5-flash", True)
        )
        self.assertEqual(response["workflow_id"], "task-123")
        self.assertEqual(response["status"], "PENDING")
        self.assertEqual(response["contract_id"], "c1")
        self.assertIn("task-123", response["status_url"])
        self.assertIn("c1", response["stream_url"])


class WorkflowStatusEndpointTests(unittest.IsolatedAsyncioTestCase):
    def _fake_async_result(self, state, result=None, info=None):
        fake = MagicMock()
        fake.state = state
        fake.result = result
        fake.info = info
        return fake

    async def test_pending_status_includes_circuit_breaker_state(self):
        with patch("celery.result.AsyncResult", return_value=self._fake_async_result("PENDING")), \
             patch.object(supervisor_api.task_ownership_store, "is_owner", return_value=True):
            from backend.governance.auth import TokenIdentity
            identity = TokenIdentity(tenant_id="tenant_a", role="admin")
            response = await supervisor_api.get_workflow_status("task-1", identity=identity)

        self.assertEqual(response["status"], "PENDING")
        self.assertIn("circuit_breakers", response)
        self.assertIn("gemini", response["circuit_breakers"])
        self.assertIn("neo4j", response["circuit_breakers"])

    async def test_success_status_surfaces_quality_grade_and_escalation(self):
        fake_result = self._fake_async_result("SUCCESS", result={
            "contract_id": "c1",
            "quality_grade": {"grade": "C", "grounded_rate": 0.8},
            "escalated": False,
            "analysis_method": "enhanced_phase2_fallback",
        })
        with patch("celery.result.AsyncResult", return_value=fake_result), \
             patch.object(supervisor_api.task_ownership_store, "is_owner", return_value=True):
            from backend.governance.auth import TokenIdentity
            identity = TokenIdentity(tenant_id="tenant_a", role="admin")
            response = await supervisor_api.get_workflow_status("task-1", identity=identity)

        self.assertEqual(response["status"], "SUCCESS")
        self.assertEqual(response["quality_grade"]["grade"], "C")
        self.assertFalse(response["escalated"])
        self.assertEqual(response["analysis_method"], "enhanced_phase2_fallback")
        self.assertIn("result", response)

    async def test_failure_status_reports_real_error(self):
        fake_result = self._fake_async_result("FAILURE", info=RuntimeError("boom"))
        with patch("celery.result.AsyncResult", return_value=fake_result), \
             patch.object(supervisor_api.task_ownership_store, "is_owner", return_value=True):
            from backend.governance.auth import TokenIdentity
            identity = TokenIdentity(tenant_id="tenant_a", role="admin")
            response = await supervisor_api.get_workflow_status("task-1", identity=identity)

        self.assertEqual(response["status"], "FAILURE")
        self.assertIn("boom", response["error"])
        self.assertIn("circuit_breakers", response)


if __name__ == "__main__":
    unittest.main()
