"""
Regression test for the same DateTime-serialization bug found at a second
(and third) call site beyond AuditLogger.get_audit_trail (already fixed and
covered by test_audit_trail_timestamp_serialization.py):

1. GET /api/intelligence/contracts/{id}/status - "last_updated" comes
   straight from c.intelligence_updated (set via datetime() in
   contract_intelligence_service.py), returned raw.
2. ErrorTracker.get_recent_errors (GET /api/audit/errors/recent) -
   "timestamp" comes from e.timestamp (set via datetime($timestamp) in
   track_error), returned raw.

Both are the identical class of bug: a neo4j.time.DateTime object left
unconverted in a dict that flows straight into an API response, which
FastAPI's default JSON encoder can't serialize properly - it falls back to
dumping the object's __dict__ internals (nonsensical fields like
"_Date__day": -2) instead of a readable ISO 8601 string.

Fixed via a single shared helper (backend/shared/utils/utils.py's
serialize_neo4j_datetime), used consistently at all three call sites now,
rather than three independent inline checks.
"""

import unittest
from unittest.mock import patch, MagicMock

from neo4j.time import DateTime

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.shared.utils.utils import serialize_neo4j_datetime
    from backend.api import contract_intelligence
    from backend.infrastructure.error_tracker import ErrorTracker

_FIXED_DATETIME = DateTime(2026, 7, 30, 23, 36, 58, 813000000)


class SerializeNeo4jDatetimeHelperTests(unittest.TestCase):
    def test_converts_real_neo4j_datetime_to_iso_string(self):
        result = serialize_neo4j_datetime(_FIXED_DATETIME)
        self.assertIsInstance(result, str)
        self.assertEqual(result, _FIXED_DATETIME.iso_format())
        self.assertNotIn("_Date__day", result)
        self.assertTrue(result.startswith("2026-07-30"))

    def test_plain_string_left_unchanged(self):
        self.assertEqual(serialize_neo4j_datetime("already-a-string"), "already-a-string")

    def test_none_left_unchanged(self):
        self.assertIsNone(serialize_neo4j_datetime(None))


class IntelligenceStatusLastUpdatedSerializationTests(unittest.TestCase):
    """GET /api/intelligence/contracts/{id}/status's last_updated field."""

    def setUp(self):
        self.fake_graph = MagicMock()
        self.fake_graph.query.return_value = [{
            "status": "completed_with_errors", "risk_score": 70.0, "risk_level": "HIGH",
            "violations_count": 2, "clauses_count": 7, "redlines_count": 2,
            "processing_time": 23.5, "updated": _FIXED_DATETIME,
        }]
        self._patcher = patch.object(contract_intelligence.repository, "graph", self.fake_graph)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_last_updated_is_a_valid_iso_string_not_raw_datetime(self):
        import asyncio
        response = asyncio.run(contract_intelligence.get_intelligence_status(
            contract_id="CNT1", tenant_id="tenant_a"
        ))

        last_updated = response["last_updated"]
        self.assertIsInstance(last_updated, str)
        self.assertNotIn("_Date__day", last_updated)
        self.assertEqual(last_updated, _FIXED_DATETIME.iso_format())

    def test_response_is_json_serializable_with_stdlib_json(self):
        import asyncio
        import json

        response = asyncio.run(contract_intelligence.get_intelligence_status(
            contract_id="CNT1", tenant_id="tenant_a"
        ))

        # Would raise TypeError before the fix - neo4j.time.DateTime has no
        # built-in json support.
        serialized = json.dumps(response)
        self.assertIn("2026-07-30", serialized)


class ErrorTrackerRecentErrorsSerializationTests(unittest.TestCase):
    """ErrorTracker.get_recent_errors (GET /api/audit/errors/recent)."""

    def test_timestamp_is_a_valid_iso_string_not_raw_datetime(self):
        tracker = ErrorTracker.__new__(ErrorTracker)
        tracker.repository = MagicMock()
        tracker.repository.graph.query.return_value = [{
            "error_id": "error_1", "error_type": "RuntimeError", "error_message": "boom",
            "category": "processing_error", "severity": "high", "operation": "test_op",
            "resource_id": "res_1", "timestamp": _FIXED_DATETIME,
        }]

        errors = tracker.get_recent_errors(limit=10)

        self.assertEqual(len(errors), 1)
        timestamp = errors[0]["timestamp"]
        self.assertIsInstance(timestamp, str)
        self.assertNotIn("_Date__day", timestamp)
        self.assertEqual(timestamp, _FIXED_DATETIME.iso_format())


if __name__ == "__main__":
    unittest.main()
