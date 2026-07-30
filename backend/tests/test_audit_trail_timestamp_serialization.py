"""
Regression test for a live end-to-end testing finding: GET /api/audit/
trail/{resource_id} returned raw neo4j.time.DateTime.__dict__ internals
(nonsensical fields like "_Date__day": -2) instead of an ISO 8601 string,
because AuditLogger.get_audit_trail returned a.timestamp (a real
neo4j.time.DateTime object from Cypher's datetime()) completely unconverted
- FastAPI's default JSON encoder doesn't know how to serialize that type
and falls back to dumping its internal attributes.
"""

import unittest
from unittest.mock import patch

from neo4j.time import DateTime

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.audit_logger import AuditLogger


class FakeGraphWithRealDateTime:
    """Returns a real neo4j.time.DateTime for a.timestamp, matching exactly
    what a real Cypher `datetime()` value looks like when it comes back
    through the driver - unlike the plain-string stand-ins used elsewhere in
    this suite's FakeGraph, this is what actually exposed the bug."""

    def __init__(self, timestamp):
        self._timestamp = timestamp

    def query(self, cypher, params=None):
        params = params or {}
        if "MATCH (a:AuditLog" in cypher:
            return [{
                "audit_id": "audit_1", "event_type": "agent_tool_call", "action": "policy_check",
                "user_id": "system", "status": "success", "timestamp": self._timestamp,
                "metadata": "{}",
            }]
        return []


class AuditTrailTimestampSerializationTests(unittest.TestCase):
    def test_timestamp_serializes_as_iso_string_not_raw_object(self):
        fixed = DateTime(2026, 7, 30, 6, 58, 9, 815000000)
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            audit_logger = AuditLogger()
        audit_logger.repository.graph = FakeGraphWithRealDateTime(fixed)

        trail = audit_logger.get_audit_trail("resource_1")

        self.assertEqual(len(trail), 1)
        timestamp = trail[0]["timestamp"]

        self.assertIsInstance(timestamp, str)
        self.assertEqual(timestamp, fixed.iso_format())
        # The exact bug: raw neo4j.time.Date/DateTime __dict__ internals
        # leaking into the response instead of a real value.
        self.assertNotIn("_Date__day", timestamp)
        self.assertTrue(timestamp.startswith("2026-07-30"))

    def test_timestamp_is_json_serializable_with_stdlib_json(self):
        import json

        fixed = DateTime(2026, 7, 30, 6, 58, 9, 815000000)
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            audit_logger = AuditLogger()
        audit_logger.repository.graph = FakeGraphWithRealDateTime(fixed)

        trail = audit_logger.get_audit_trail("resource_1")

        # This would raise TypeError("Object of type DateTime is not JSON
        # serializable") before the fix, since json.dumps has no built-in
        # support for neo4j.time.DateTime (matching what FastAPI's default
        # encoder falls back to __dict__ for instead of raising outright).
        serialized = json.dumps(trail)
        self.assertIn("2026-07-30", serialized)

    def test_non_datetime_timestamp_left_unchanged(self):
        """FakeGraph stand-ins elsewhere in this suite return a plain
        string for timestamp (e.g. reusing the audit_id) - the fix must not
        assume every timestamp value is a neo4j.time.DateTime."""
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            audit_logger = AuditLogger()
        audit_logger.repository.graph = FakeGraphWithRealDateTime("audit_1_plain_string_stand_in")

        trail = audit_logger.get_audit_trail("resource_1")

        self.assertEqual(trail[0]["timestamp"], "audit_1_plain_string_stand_in")


if __name__ == "__main__":
    unittest.main()
