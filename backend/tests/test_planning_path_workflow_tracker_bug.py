"""
Regression test for a real, previously-undiscovered production bug found
live while verifying the Supervisor rebuild: IntelligenceOrchestrator.
_analyze_with_planning never called workflow_tracker.start_workflow(),
even though PlanExecutionEngine.execute_plan unconditionally calls
workflow_tracker.complete_workflow() at the end and its own comment
claimed "planning agent already started it." complete_workflow() computes
datetime.now() - self.workflow_start_time, which raised
"unsupported operand type(s) for -: 'datetime.datetime' and 'NoneType'"
whenever workflow_start_time was still None.

Confirmed live (worker logs) that every single planning-path run in this
whole engagement had been silently hitting this exception and falling
back to the traditional workflow via _analyze_with_planning's own
except-and-fallback - never a hard failure, just a silent, permanent
downgrade of the documented default path. This is exactly why it went
undetected: the fallback produces a structurally valid, plausible-looking
result (the traditional workflow's own node_status keys, e.g.
"clause_extraction" instead of the planning path's "extract_clauses"),
not an error.

This test uses the REAL workflow_tracker singleton (not mocked) and
explicitly resets it to the exact broken-precondition state
(workflow_start_time = None) before running the real
_analyze_with_planning path, proving complete_workflow() no longer raises
and the planning path's own result (quality_grade et al.) is what's
actually returned - not a silent fallback.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator
    from backend.agents.planning.execution_engine import PlanExecutionEngine, ExecutionResult
    from backend.agents.planning.planning_agent import PlanningAgentFactory, StepType
    from backend.agents.agent_workflow_tracker import workflow_tracker


def _fake_output_for(step_type: StepType):
    """Real _update_context_with_result branches expect different shapes
    per step type (e.g. CUAD_MITIGATION's output must be a dict with
    .get() - a bare [] there raises AttributeError, unrelated to the real
    bug this file tests). Mirrors each step type's real output shape
    closely enough for _update_context_with_result to run cleanly."""
    if step_type == StepType.CUAD_MITIGATION:
        return {"cuad_deviations": [], "jurisdiction_info": {}, "precedent_matches": [], "analysis_method": "optimized_phase3"}
    if step_type == StepType.CHECK_POLICIES:
        return {"violations": [], "failed_clause_ids": [], "status": "success"}
    if step_type in (StepType.ASSESS_RISK, StepType.VALIDATE_RESULTS):
        return {}
    return []


class PlanningPathWorkflowTrackerBugTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Exactly the broken precondition: a fresh process/worker that has
        # never called start_workflow() at all.
        workflow_tracker.workflow_start_time = None
        workflow_tracker.executions = []

    async def test_analyze_with_planning_does_not_crash_on_a_never_started_tracker(self):
        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None  # bypassing __init__, which normally sets this
        orchestrator.planning_agent = PlanningAgentFactory.create_planning_agent()
        orchestrator.execution_engine = PlanExecutionEngine()

        async def fake_execute_step(step, context):
            return ExecutionResult(
                step_id=step.step_id, success=True, output_data=_fake_output_for(step.step_type),
                execution_time_ms=1, confidence_score=0.9,
            )
        orchestrator.execution_engine.step_executor.execute_step = fake_execute_step

        with patch("backend.infrastructure.audit_logger.AuditLogger"), \
             patch("backend.agents.planning.execution_engine.publish_step_progress"):
            # Must not raise - this is the exact call chain that crashed
            # inside workflow_tracker.complete_workflow() before the fix.
            result = await orchestrator._analyze_with_planning("contract text", contract_id="c1", tenant_id="t1")

        # Proves the REAL planning-path result came back, not a silent
        # fallback to the traditional workflow - only PlanExecutionEngine's
        # _format_final_results sets "planned_execution".
        self.assertTrue(result.get("planned_execution"), "must be the real planning-path result, not a swallowed fallback")
        self.assertIn("quality_grade", result)

    async def test_workflow_start_time_is_actually_set_before_execute_plan_runs(self):
        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None  # bypassing __init__, which normally sets this
        orchestrator.planning_agent = PlanningAgentFactory.create_planning_agent()
        orchestrator.execution_engine = PlanExecutionEngine()

        observed_start_time = {}

        async def fake_execute_step(step, context):
            # By the time any step runs, start_workflow() must already
            # have set a real timestamp - this is the exact assumption
            # execute_plan's complete_workflow() call at the end relies on.
            observed_start_time["value"] = workflow_tracker.workflow_start_time
            return ExecutionResult(
                step_id=step.step_id, success=True, output_data=[],
                execution_time_ms=1, confidence_score=0.9,
            )
        orchestrator.execution_engine.step_executor.execute_step = fake_execute_step

        with patch("backend.infrastructure.audit_logger.AuditLogger"), \
             patch("backend.agents.planning.execution_engine.publish_step_progress"):
            await orchestrator._analyze_with_planning("contract text", contract_id="c1", tenant_id="t1")

        self.assertIsNotNone(observed_start_time.get("value"))


if __name__ == "__main__":
    unittest.main()
