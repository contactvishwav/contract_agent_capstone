"""
Tests for Supervisor rebuild step 3: real Redis pub/sub progress
publishing (backend/agents/supervisor/progress_publisher.py) and its
wiring into PlanExecutionEngine.execute_plan.
"""

import json
import unittest
from unittest.mock import MagicMock, call, patch

from backend.agents.supervisor.progress_publisher import (
    channel_name,
    publish_step_progress,
    subscribe,
)


class ProgressPublisherUnitTests(unittest.TestCase):
    def test_publish_sends_real_json_to_the_contract_scoped_channel(self):
        fake_client = MagicMock()
        with patch("backend.agents.supervisor.progress_publisher.cache") as mock_cache:
            mock_cache.redis_client = fake_client
            publish_step_progress("c1", "tenant_a", "extract_clauses", "success", step_id="s1")

        fake_client.publish.assert_called_once()
        channel, message = fake_client.publish.call_args.args
        self.assertEqual(channel, channel_name("c1", "tenant_a"))
        payload = json.loads(message)
        self.assertEqual(payload["step_type"], "extract_clauses")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["step_id"], "s1")
        self.assertIn("timestamp", payload)

    def test_channel_namespace_differs_across_tenants(self):
        self.assertNotEqual(
            channel_name("same-contract", "tenant_a"),
            channel_name("same-contract", "tenant_b"),
        )

    def test_no_contract_id_is_a_no_op(self):
        fake_client = MagicMock()
        with patch("backend.agents.supervisor.progress_publisher.cache") as mock_cache:
            mock_cache.redis_client = fake_client
            publish_step_progress(None, "tenant_a", "extract_clauses", "success")

        fake_client.publish.assert_not_called()

    def test_a_publish_failure_never_raises(self):
        fake_client = MagicMock()
        fake_client.publish.side_effect = RuntimeError("redis down")
        with patch("backend.agents.supervisor.progress_publisher.cache") as mock_cache:
            mock_cache.redis_client = fake_client
            publish_step_progress("c1", "tenant_a", "extract_clauses", "success")  # must not raise

    def test_subscribe_subscribes_to_the_contract_scoped_channel(self):
        fake_pubsub = MagicMock()
        fake_client = MagicMock()
        fake_client.pubsub.return_value = fake_pubsub
        with patch("backend.agents.supervisor.progress_publisher.cache") as mock_cache:
            mock_cache.redis_client = fake_client
            result = subscribe("c1", "tenant_a")

        fake_pubsub.subscribe.assert_called_once_with(channel_name("c1", "tenant_a"))
        self.assertIs(result, fake_pubsub)

    def test_in_memory_fallback_publish_and_pubsub_are_safe_no_ops(self):
        # Real InMemoryCache (no mocking) - proves the fallback path used
        # when Redis is unreachable doesn't crash the caller.
        from backend.shared.cache.redis_cache import InMemoryCache

        fallback = InMemoryCache()
        self.assertEqual(fallback.publish("some_channel", "msg"), 0)
        pubsub = fallback.pubsub()
        pubsub.subscribe("some_channel")
        self.assertIsNone(pubsub.get_message(timeout=0.01))
        pubsub.close()


class ExecutePlanPublishesProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_plan_publishes_started_per_step_and_complete(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.planning.execution_engine import PlanExecutionEngine, ExecutionResult
            from backend.agents.planning.planning_agent import ExecutionPlan, ExecutionStep, StepType, PlanningStrategy
            from backend.agents.agent_workflow_tracker import workflow_tracker

        engine = PlanExecutionEngine()

        async def fake_execute_step(step, context):
            return ExecutionResult(
                step_id=step.step_id, success=True, output_data=[],
                execution_time_ms=1, confidence_score=0.9,
            )

        engine.step_executor.execute_step = fake_execute_step
        workflow_tracker.start_workflow()

        plan = ExecutionPlan(
            plan_id="p1", query="analyze", strategy=PlanningStrategy.SIMPLE,
            steps=[ExecutionStep(step_id="s1", step_type=StepType.EXTRACT_CLAUSES, description="extract")],
            estimated_duration=1, confidence_score=1.0,
        )

        with patch("backend.agents.planning.execution_engine.publish_step_progress") as mock_publish, \
             patch("backend.infrastructure.audit_logger.AuditLogger"):
            await engine.execute_plan(plan, "contract text", contract_id="c1", tenant_id="t1")

        calls = mock_publish.call_args_list
        # First call: workflow started
        self.assertEqual(calls[0].args[:4], ("c1", "t1", "workflow", "started"))
        # A per-step call for the one step in this plan
        self.assertEqual(calls[1].args[:4], ("c1", "t1", "extract_clauses", "success"))
        # Final call: workflow complete
        self.assertEqual(calls[-1].args[:4], ("c1", "t1", "workflow", "complete"))

    async def test_execute_plan_publishes_workflow_failed_on_hard_abort(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.planning.execution_engine import PlanExecutionEngine
            from backend.agents.planning.planning_agent import ExecutionPlan, ExecutionStep, StepType, PlanningStrategy
            from backend.agents.agent_workflow_tracker import workflow_tracker

        engine = PlanExecutionEngine()

        async def broken_execute_step(step, context):
            raise RuntimeError("hard abort")

        engine.step_executor.execute_step = broken_execute_step
        workflow_tracker.start_workflow()

        plan = ExecutionPlan(
            plan_id="p2", query="analyze", strategy=PlanningStrategy.SIMPLE,
            steps=[ExecutionStep(step_id="s1", step_type=StepType.EXTRACT_CLAUSES, description="extract")],
            estimated_duration=1, confidence_score=1.0,
        )

        with patch("backend.agents.planning.execution_engine.publish_step_progress") as mock_publish, \
             patch("backend.infrastructure.audit_logger.AuditLogger"):
            await engine.execute_plan(plan, "contract text", contract_id="c1", tenant_id="t1")

        calls = mock_publish.call_args_list
        self.assertEqual(calls[-1].args[:4], ("c1", "t1", "workflow", "failed"))
        self.assertEqual(calls[-1].kwargs["error_type"], "RuntimeError")
        self.assertNotIn("error", calls[-1].kwargs)


if __name__ == "__main__":
    unittest.main()
