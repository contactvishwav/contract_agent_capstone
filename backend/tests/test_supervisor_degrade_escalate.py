"""
Tests for Supervisor rebuild step 2: surfacing two recovery signals that
were either discarded or entirely missing before.

1. "Degrade" - _execute_cuad_mitigation already runs a real Phase3 ->
   Phase2 -> Phase1 fallback cascade and computes which tier actually
   ran (analysis_method), but _update_context_with_result discarded it
   immediately - a degraded-but-successful analysis was indistinguishable
   from a full Phase-3 one in the API response. Now threaded through to
   result["analysis_method"].

2. "Escalate" - genuinely new: any step that came back "failed" (not
   just "partial") now marks the workflow result["escalated"] = True and
   writes one roll-up WORKFLOW_ESCALATION audit event, queryable via the
   existing GET /api/audit/trail/{contract_id} route.
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.planning.execution_engine import PlanExecutionEngine, ExecutionResult
    from backend.agents.planning.planning_agent import ExecutionPlan, ExecutionStep, StepType, PlanningStrategy


class AnalysisMethodSurfacingTests(unittest.TestCase):
    def test_cuad_mitigation_result_threads_analysis_method_into_context(self):
        engine = PlanExecutionEngine()
        engine.execution_context = {}
        step = ExecutionStep(step_id="s1", step_type=StepType.CUAD_MITIGATION, description="cuad")
        result = ExecutionResult(
            step_id="s1", success=True,
            output_data={
                "cuad_deviations": [], "jurisdiction_info": {}, "precedent_matches": [],
                "analysis_method": "fallback_phase1",
            },
            execution_time_ms=1, confidence_score=0.9,
        )

        engine._update_context_with_result(step, result)

        self.assertEqual(engine.execution_context["cuad_analysis_method"], "fallback_phase1")

    def test_analysis_method_appears_in_final_results(self):
        engine = PlanExecutionEngine()
        engine.execution_context = {"cuad_analysis_method": "enhanced_phase2_fallback"}

        result = engine._format_final_results({"extract_clauses": "success"})

        self.assertEqual(result["analysis_method"], "enhanced_phase2_fallback")

    def test_analysis_method_is_none_when_cuad_mitigation_never_ran(self):
        engine = PlanExecutionEngine()
        engine.execution_context = {}

        result = engine._format_final_results({"extract_clauses": "success"})

        self.assertIsNone(result["analysis_method"])


class EscalationFlagTests(unittest.TestCase):
    def test_escalated_true_when_any_step_failed(self):
        engine = PlanExecutionEngine()
        engine.execution_context = {}

        result = engine._format_final_results({"extract_clauses": "success", "assess_risk": "failed"})

        self.assertTrue(result["escalated"])

    def test_escalated_false_when_nothing_failed_even_with_partial(self):
        engine = PlanExecutionEngine()
        engine.execution_context = {}

        result = engine._format_final_results({"extract_clauses": "success", "check_policies": "partial"})

        self.assertFalse(result["escalated"], "partial alone must not trigger escalation - that's what grade C/D already communicate")

    def test_error_results_are_always_escalated(self):
        engine = PlanExecutionEngine()

        result = engine._format_error_results("boom", {})

        self.assertTrue(result["escalated"])


class EscalationAuditLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_plan_logs_workflow_escalation_when_a_step_fails(self):
        engine = PlanExecutionEngine()

        async def fake_execute_step(step, context):
            if step.step_type == StepType.ASSESS_RISK:
                return ExecutionResult(
                    step_id=step.step_id, success=False, output_data=None,
                    execution_time_ms=1, confidence_score=0.0, error_message="boom",
                )
            return ExecutionResult(
                step_id=step.step_id, success=True, output_data=[],
                execution_time_ms=1, confidence_score=0.9,
            )

        engine.step_executor.execute_step = fake_execute_step

        from backend.agents.agent_workflow_tracker import workflow_tracker
        workflow_tracker.start_workflow()

        plan = ExecutionPlan(
            plan_id="p1", query="analyze", strategy=PlanningStrategy.SIMPLE,
            steps=[
                ExecutionStep(step_id="s1", step_type=StepType.EXTRACT_CLAUSES, description="extract"),
                ExecutionStep(step_id="s2", step_type=StepType.ASSESS_RISK, description="assess"),
            ],
            estimated_duration=1, confidence_score=1.0,
        )

        mock_audit_logger = MagicMock()
        with patch("backend.infrastructure.audit_logger.AuditLogger", return_value=mock_audit_logger):
            result = await engine.execute_plan(plan, "contract text", contract_id="c1", tenant_id="t1")

        self.assertTrue(result["escalated"])
        mock_audit_logger.log_event.assert_called_once()
        call_kwargs = mock_audit_logger.log_event.call_args.kwargs
        self.assertEqual(call_kwargs["resource_id"], "c1")
        self.assertEqual(call_kwargs["tenant_id"], "t1")
        self.assertEqual(call_kwargs["action"], "workflow_escalation")

    async def test_execute_plan_does_not_log_escalation_when_nothing_failed(self):
        engine = PlanExecutionEngine()

        async def fake_execute_step(step, context):
            return ExecutionResult(
                step_id=step.step_id, success=True, output_data=[],
                execution_time_ms=1, confidence_score=0.9,
            )

        engine.step_executor.execute_step = fake_execute_step

        from backend.agents.agent_workflow_tracker import workflow_tracker
        workflow_tracker.start_workflow()

        plan = ExecutionPlan(
            plan_id="p2", query="analyze", strategy=PlanningStrategy.SIMPLE,
            steps=[ExecutionStep(step_id="s1", step_type=StepType.EXTRACT_CLAUSES, description="extract")],
            estimated_duration=1, confidence_score=1.0,
        )

        mock_audit_logger = MagicMock()
        with patch("backend.infrastructure.audit_logger.AuditLogger", return_value=mock_audit_logger):
            result = await engine.execute_plan(plan, "contract text", contract_id="c1", tenant_id="t1")

        self.assertFalse(result["escalated"])
        mock_audit_logger.log_event.assert_not_called()

    async def test_a_broken_audit_logger_does_not_break_the_real_result(self):
        # _log_escalation_if_needed must never let a logging failure turn
        # an already-computed real analysis result into a hard error.
        engine = PlanExecutionEngine()

        async def fake_execute_step(step, context):
            return ExecutionResult(
                step_id=step.step_id, success=False, output_data=None,
                execution_time_ms=1, confidence_score=0.0, error_message="boom",
            )

        engine.step_executor.execute_step = fake_execute_step

        from backend.agents.agent_workflow_tracker import workflow_tracker
        workflow_tracker.start_workflow()

        plan = ExecutionPlan(
            plan_id="p3", query="analyze", strategy=PlanningStrategy.SIMPLE,
            steps=[ExecutionStep(step_id="s1", step_type=StepType.EXTRACT_CLAUSES, description="extract")],
            estimated_duration=1, confidence_score=1.0,
        )

        with patch("backend.infrastructure.audit_logger.AuditLogger", side_effect=RuntimeError("neo4j down")):
            result = await engine.execute_plan(plan, "contract text", contract_id="c1", tenant_id="t1")

        self.assertTrue(result["escalated"])
        self.assertEqual(result["node_status"]["extract_clauses"], "failed")


if __name__ == "__main__":
    unittest.main()
