"""
Regression test: several places in the analysis pipeline masked a partial
or total node failure as a plausible-looking success:

1. RiskCalculatorTool._run's except path returned a fabricated
   {"overall_risk_score": 50.0, "risk_level": "MEDIUM", ...} -
   indistinguishable from a real MEDIUM-risk result.
2. IntelligenceOrchestrator._generate_redlines's except path set
   "is_complete": True even though redline generation had just failed.
3. PlanExecutionEngine always returned "processing_complete": True
   regardless of whether individual steps failed - per-step
   success/failure was tracked in step_results but never surfaced.
4. ContractIntelligence (and the API route) always reported
   analysis_complete as True/omitted node status entirely, so a caller had
   no way to tell a genuine result from one produced despite a failure.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.intelligence_tools import RiskCalculatorTool
    from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator
    from backend.agents.planning.execution_engine import PlanExecutionEngine, StepExecutor, ExecutionResult
    from backend.agents.planning.planning_agent import ExecutionPlan, ExecutionStep, StepType, PlanningStrategy
    from backend.application.services.contract_intelligence_service import ContractIntelligenceService

_audit_logger_patcher = patch("backend.agents.intelligence_tools.AuditLogger", return_value=MagicMock())
_audit_logger_patcher.start()


class RiskCalculatorFailureTests(unittest.TestCase):
    def test_except_path_reports_error_not_fabricated_medium(self):
        # violations_json is not valid JSON -> json.loads raises inside the try block
        result = json.loads(RiskCalculatorTool()._run("[]", "not valid json"))

        self.assertEqual(result["risk_level"], "ERROR")
        self.assertIsNone(result["overall_risk_score"])
        self.assertNotEqual(result["risk_level"], "MEDIUM")
        self.assertNotEqual(result["overall_risk_score"], 50.0)


class RedlineNodeFailureTests(unittest.TestCase):
    def test_generate_redlines_except_path_marks_incomplete(self):
        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None  # bypassing __init__, which normally sets this
        state = {
            "contract_text": "text", "extracted_clauses": [], "policy_violations": [],
            "risk_data": {}, "contract_id": "c1", "tenant_id": "t1", "node_status": {},
        }

        with patch("backend.agents.contract_intelligence_agents.RedlineGeneratorTool") as MockTool:
            MockTool.return_value._run.side_effect = RuntimeError("redline generator down")
            result_state = orchestrator._generate_redlines(state)

        self.assertFalse(result_state["is_complete"])
        self.assertEqual(result_state["node_status"]["redline_generation"], "error")


class PlanExecutionEngineFailureTests(unittest.TestCase):
    def test_step_failure_reported_honestly_in_final_results(self):
        engine = PlanExecutionEngine()

        async def fake_execute_step(step, context):
            if step.step_type == StepType.ASSESS_RISK:
                return ExecutionResult(
                    step_id=step.step_id, success=False, output_data=None,
                    execution_time_ms=1, confidence_score=0.0, error_message="boom",
                )
            return ExecutionResult(
                step_id=step.step_id, success=True,
                output_data=[] if step.step_type == StepType.EXTRACT_CLAUSES else {},
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

        import asyncio
        result = asyncio.run(engine.execute_plan(plan, "contract text", contract_id="c1", tenant_id="t1"))

        self.assertFalse(result["processing_complete"])
        self.assertEqual(result["node_status"]["assess_risk"], "failed")
        self.assertEqual(result["node_status"]["extract_clauses"], "success")


class EndToEndPartialFailureTests(unittest.TestCase):
    def test_convert_to_domain_entities_surfaces_dishonest_completion_state(self):
        service = ContractIntelligenceService.__new__(ContractIntelligenceService)
        analysis_result = {
            "clauses": [], "violations": [],
            "risk_assessment": {"overall_risk_score": None, "risk_level": "ERROR", "critical_issues": [], "recommendations": []},
            "redlines": [],
            "node_status": {"clause_extraction": "success", "risk_calculation": "error"},
            "processing_complete": False,
        }

        intelligence = service._convert_to_domain_entities(analysis_result)

        self.assertFalse(intelligence.processing_complete)
        self.assertEqual(intelligence.node_status["risk_calculation"], "error")


if __name__ == "__main__":
    unittest.main()
