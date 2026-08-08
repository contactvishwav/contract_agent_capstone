import json
import unittest
from unittest.mock import MagicMock

from backend.infrastructure.agent_audit_service import AgentAuditService
from backend.infrastructure.audit_logger import AuditEventType, AuditLogger, AuditScope
from backend.infrastructure.error_tracker import ErrorCategory, ErrorContext, ErrorSeverity, ErrorTracker


class RecordingGraph:
    def __init__(self):
        self.calls = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))
        if "MERGE (a:AuditLog" in cypher:
            return [{"audit_id": (params or {})["audit_id"]}]
        if "MERGE (e:ErrorLog" in cypher:
            return [{"error_id": (params or {})["error_id"]}]
        return []


def audit_logger_with(graph):
    logger = AuditLogger.__new__(AuditLogger)
    logger.repository = MagicMock()
    logger.repository.graph = graph
    return logger


def error_tracker_with(graph):
    tracker = ErrorTracker.__new__(ErrorTracker)
    tracker.repository = MagicMock()
    tracker.repository.graph = graph
    return tracker


class AuditTenantAttributionTests(unittest.TestCase):
    def test_missing_tenant_is_not_written_or_misattributed(self):
        graph = RecordingGraph()
        logger = audit_logger_with(graph)
        result = logger.log_event(
            AuditEventType.PROCESSING_ERROR, "resource", "operation"
        )
        self.assertEqual(result, "")
        self.assertEqual(graph.calls, [])

    def test_system_event_is_explicit_and_not_tenant_looking(self):
        graph = RecordingGraph()
        logger = audit_logger_with(graph)
        logger.log_event(
            AuditEventType.SECURITY_VIOLATION,
            "mcp_server",
            "missing_identity",
            scope=AuditScope.SYSTEM,
        )
        params = graph.calls[0][1]
        self.assertIsNone(params["tenant_id"])
        self.assertEqual(params["scope"], "system")
        self.assertNotIn("demo", json.dumps(params).lower())

    def test_agent_audit_omits_prompt_arguments_and_result_content(self):
        graph = RecordingGraph()
        logger = audit_logger_with(graph)
        service = AgentAuditService(
            logger, tenant_id="tenant_a", correlation_id="corr-1"
        )
        service.log_user_interaction("user", "top secret prompt", "session")
        service.log_tool_execution(
            "search", {"query": "secret clause", "tenant_id": "tenant_a"},
            "secret result", "session"
        )
        serialized = json.dumps([params for _, params in graph.calls])
        self.assertNotIn("top secret prompt", serialized)
        self.assertNotIn("secret clause", serialized)
        self.assertNotIn("secret result", serialized)
        self.assertTrue(all(params["tenant_id"] == "tenant_a" for _, params in graph.calls))

    def test_audit_read_query_contains_tenant_predicate(self):
        graph = RecordingGraph()
        logger = audit_logger_with(graph)
        logger.get_audit_trail("resource", "tenant_a")
        cypher, params = graph.calls[0]
        self.assertIn("tenant_id: $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_a")


class ErrorTenantAttributionTests(unittest.TestCase):
    def test_missing_tenant_error_is_not_written(self):
        graph = RecordingGraph()
        tracker = error_tracker_with(graph)
        tracker.track_error(
            RuntimeError("sensitive provider payload"),
            ErrorCategory.PROCESSING_ERROR,
            ErrorSeverity.HIGH,
            ErrorContext(operation="analysis"),
        )
        self.assertEqual(graph.calls, [])

    def test_tenant_error_uses_safe_message_and_scope(self):
        graph = RecordingGraph()
        tracker = error_tracker_with(graph)
        tracker.track_error(
            RuntimeError("sensitive provider payload"),
            ErrorCategory.PROCESSING_ERROR,
            ErrorSeverity.HIGH,
            ErrorContext(operation="analysis", tenant_id="tenant_a"),
        )
        params = graph.calls[0][1]
        self.assertEqual(params["tenant_id"], "tenant_a")
        self.assertEqual(params["scope"], "tenant")
        self.assertEqual(params["error_message"], "RuntimeError")
        self.assertNotIn("sensitive provider payload", json.dumps(params))

    def test_error_read_queries_are_tenant_scoped(self):
        graph = RecordingGraph()
        tracker = error_tracker_with(graph)
        tracker.get_error_statistics("tenant_a")
        tracker.get_recent_errors("tenant_a")
        for cypher, params in graph.calls:
            self.assertIn("tenant_id: $tenant_id", cypher)
            self.assertEqual(params["tenant_id"], "tenant_a")


if __name__ == "__main__":
    unittest.main()
