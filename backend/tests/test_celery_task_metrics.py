"""
Regression tests for reliability/observability audit findings #8 and #10:

#8 - task_acks_late/task_reject_on_worker_lost were not set, so a worker
killed mid-analysis (deploy, OOM) would silently drop the task instead of
requeuing it. Verified here at the config level (the actual kill-and-
requeue behavior is Celery/broker runtime behavior, not something a unit
test can observe without a real broker).

#10 - Celery task-state counts weren't exposed anywhere. record_task_state/
get_task_state_counts (backend/shared/monitoring/celery_task_metrics.py)
are wired up as Celery signal handlers (backend/celery_app.py) so they
work regardless of which process (backend vs. worker) actually runs the
task, same Redis-backed cross-process pattern as finding #1's
LLMUsageTracker.
"""

import unittest
from unittest.mock import patch, MagicMock, AsyncMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.celery_app import celery_app
    from backend.tasks import analyze_contract_task
    from backend.shared.monitoring.celery_task_metrics import (
        record_task_state,
        get_task_state_counts,
    )
    from backend.shared.cache.redis_cache import cache, InMemoryCache


def _fake_intelligence():
    from types import SimpleNamespace
    return SimpleNamespace(
        processing_complete=True, node_status={}, processing_time=1.0,
        clauses=[], violations=[],
        risk_assessment=SimpleNamespace(
            overall_risk_score=1.0, risk_level="LOW",
            critical_issues=[], critical_issue_details=[], recommendations=[],
        ),
        redlines=[], cuad_deviations=[], jurisdiction_info={}, precedent_matches=[],
        # Supervisor rebuild fields - see domain/entities.py's
        # ContractIntelligence and tasks.py's _intelligence_to_response_dict.
        quality_grade={}, escalated=False, analysis_method=None,
    )


class CeleryGracefulShutdownConfigTests(unittest.TestCase):
    def test_task_acks_late_is_enabled(self):
        """Without this, Celery's default acks a task the moment it's
        received, before it runs - a worker killed mid-task loses it
        outright instead of it going back on the queue."""
        self.assertTrue(celery_app.conf.task_acks_late)

    def test_task_reject_on_worker_lost_is_enabled(self):
        self.assertTrue(celery_app.conf.task_reject_on_worker_lost)


class CeleryTaskMetricsRedisBackedTests(unittest.TestCase):
    def setUp(self):
        cache.redis_client = InMemoryCache()

    def test_record_and_read_round_trip(self):
        record_task_state("analyze_contract", "started")
        record_task_state("analyze_contract", "started")
        record_task_state("analyze_contract", "success")

        counts = get_task_state_counts()
        self.assertEqual(counts["analyze_contract"]["started"], 2)
        self.assertEqual(counts["analyze_contract"]["success"], 1)

    def test_two_independent_readers_share_state(self):
        """Cross-process visibility proof (mirrors
        test_llm_usage_tracker_redis_backed.py's proof for finding #1):
        the backend process reading metrics sees what the worker process
        recorded, because both go through the same Redis-backed client,
        not separate in-process state."""
        shared_backing_store = InMemoryCache()
        with patch.object(cache, "redis_client", shared_backing_store):
            record_task_state("analyze_contract", "failure")

        with patch.object(cache, "redis_client", shared_backing_store):
            counts = get_task_state_counts()

        self.assertEqual(counts["analyze_contract"]["failure"], 1)

    def test_recording_never_raises_even_if_redis_is_broken(self):
        broken_client = MagicMock()
        broken_client.sadd.side_effect = Exception("redis down")
        with patch.object(cache, "redis_client", broken_client):
            record_task_state("analyze_contract", "started")  # must not raise

    def test_reading_never_raises_even_if_redis_is_broken(self):
        broken_client = MagicMock()
        broken_client.smembers.side_effect = Exception("redis down")
        with patch.object(cache, "redis_client", broken_client):
            counts = get_task_state_counts()  # must not raise
        self.assertEqual(counts, {})


class CelerySignalsRecordRealTaskExecutionTests(unittest.TestCase):
    """End-to-end proof: running the real (eager) analyze_contract_task -
    not calling record_task_state directly - actually produces metrics,
    confirming the signal wiring in celery_app.py works."""

    def setUp(self):
        cache.redis_client = InMemoryCache()

    def test_successful_eager_task_records_started_and_success(self):
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(return_value=_fake_intelligence())

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            analyze_contract_task.delay("CONTRACT_1", "tenant_a")

        counts = get_task_state_counts()
        self.assertEqual(counts["analyze_contract"]["started"], 1)
        self.assertEqual(counts["analyze_contract"]["success"], 1)

    def test_failed_eager_task_records_failure(self):
        fake_service = MagicMock()
        fake_service.analyze_contract_by_id = AsyncMock(return_value=None)

        with patch(
            "backend.application.services.contract_intelligence_service.ContractIntelligenceServiceFactory.create_service",
            return_value=fake_service,
        ):
            analyze_contract_task.apply(args=("MISSING_CONTRACT", "tenant_a"))

        counts = get_task_state_counts()
        self.assertEqual(counts["analyze_contract"]["started"], 1)
        self.assertEqual(counts["analyze_contract"]["failure"], 1)


if __name__ == "__main__":
    unittest.main()
