"""
Regression test: the four intelligence-analysis tools (ClauseDetectorTool,
PolicyCheckerTool, RiskCalculatorTool, RedlineGeneratorTool) - the shared
layer both the traditional LangGraph workflow AND the default planning
(PlanExecutionEngine) path call into - never wrote an AuditLogger event.
A completed analysis had no retrievable record of what was extracted or why
a violation/risk was flagged, for either orchestration path.

Uses the FakeGraph pattern from test_audit_validation_error_tracking.py to
exercise the real log_event/get_audit_trail round trip deterministically.
"""

import json
import unittest
from collections import Counter
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.audit_logger import AuditLogger
    from backend.agents.intelligence_tools import (
        ClauseDetectorTool, PolicyCheckerTool, RiskCalculatorTool, RedlineGeneratorTool
    )


class FakeGraph:
    """Same minimal AuditLog-aware fake used in test_audit_validation_error_tracking.py."""

    def __init__(self):
        self.audit_logs = []

    def query(self, cypher, params=None):
        params = params or {}
        if "MERGE (a:AuditLog" in cypher:
            self.audit_logs.append(dict(params))
            return [{"audit_id": params["audit_id"]}]
        if "MATCH (a:AuditLog" in cypher:
            matches = [r for r in self.audit_logs if r["resource_id"] == params["resource_id"]]
            matches = matches[-params.get("limit", 100):][::-1]
            return [
                {
                    "audit_id": r["audit_id"], "event_type": r["event_type"], "action": r["action"],
                    "user_id": r["user_id"], "status": r["status"], "timestamp": r["audit_id"],
                    "metadata": r["metadata"],
                }
                for r in matches
            ]
        return []


def _shared_fake_graph_logger():
    """Build an AuditLogger backed by a FakeGraph, and monkeypatch AuditLogger()
    construction (used inline inside each tool's _run) to keep returning an
    AuditLogger sharing that same FakeGraph, so every fresh AuditLogger()
    instance inside the tools writes to (and can be read back from) one
    in-memory store for the duration of the test.

    AuditLogger()'s constructor lazily builds a Neo4jContractRepository,
    which imports (and, on first import in the process, constructs) a real
    module-level Neo4jGraph - so construction itself must happen inside the
    same patch used elsewhere in this suite (test_audit_validation_error_
    tracking.py's _with_fake_graph), not just around the module import.
    """
    shared_graph = FakeGraph()
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        real_logger = AuditLogger()
    real_logger.repository.graph = shared_graph

    patcher = patch("backend.agents.intelligence_tools.AuditLogger", return_value=real_logger)
    patcher.start()
    return real_logger, patcher


class ClauseDetectorAuditTests(unittest.TestCase):
    def test_success_logs_audit_event(self):
        fake_logger, patcher = _shared_fake_graph_logger()
        try:
            class FakeLLM:
                def with_structured_output(self, schema):
                    return self

                def invoke(self, prompt):
                    from backend.agents.llm_extraction_service import _LLMExtractionResponse, _LLMExtractedClause
                    return _LLMExtractionResponse(clauses=[
                        _LLMExtractedClause(clause_type="Governing Law", extracted_text="California law applies.", confidence=0.9)
                    ])

            tool = ClauseDetectorTool(llm=FakeLLM())
            tool._run("Some contract text.", contract_id="contract_1", tenant_id="tenant_1")

            trail = fake_logger.get_audit_trail("contract_1")
            self.assertEqual(len(trail), 1)
            self.assertEqual(trail[0]["action"], "clause_extraction")
            self.assertEqual(trail[0]["status"], "success")
        finally:
            patcher.stop()

    def test_failure_logs_audit_event_with_failure_status(self):
        fake_logger, patcher = _shared_fake_graph_logger()
        try:
            class BrokenLLM:
                def with_structured_output(self, schema):
                    raise RuntimeError("LLM unavailable")

            tool = ClauseDetectorTool(llm=BrokenLLM())
            tool._run("Some contract text.", contract_id="contract_2", tenant_id="tenant_1")

            trail = fake_logger.get_audit_trail("contract_2")
            self.assertEqual(len(trail), 1)
            self.assertEqual(trail[0]["status"], "failure")
        finally:
            patcher.stop()


class OtherToolsAuditTests(unittest.TestCase):
    def test_policy_checker_logs_audit_event(self):
        fake_logger, patcher = _shared_fake_graph_logger()
        # Patch rule resolution to a deterministic empty list - this test
        # only cares that the audit event is written correctly, not about
        # policy content, and avoids depending on PolicyRepository's real
        # Neo4j behavior against whatever fake graph the session happens to
        # have cached.
        rules_patcher = patch("backend.agents.intelligence_tools.get_applicable_rules", return_value=[])
        rules_patcher.start()
        try:
            clauses = [{"clause_type": "Non-Compete", "content": "Employee shall not compete for 5 years."}]
            PolicyCheckerTool()._run(json.dumps(clauses), contract_id="contract_3", tenant_id="tenant_1")

            trail = fake_logger.get_audit_trail("contract_3")
            self.assertEqual(len(trail), 1)
            self.assertEqual(trail[0]["action"], "policy_check")
            self.assertEqual(trail[0]["status"], "success")
        finally:
            patcher.stop()
            rules_patcher.stop()

    def test_risk_calculator_logs_audit_event(self):
        fake_logger, patcher = _shared_fake_graph_logger()
        try:
            RiskCalculatorTool()._run("[]", "[]", contract_id="contract_4", tenant_id="tenant_1")

            trail = fake_logger.get_audit_trail("contract_4")
            self.assertEqual(len(trail), 1)
            self.assertEqual(trail[0]["action"], "risk_calculation")
        finally:
            patcher.stop()

    def test_redline_generator_logs_audit_event(self):
        fake_logger, patcher = _shared_fake_graph_logger()
        try:
            RedlineGeneratorTool()._run("[]", contract_id="contract_5", tenant_id="tenant_1")

            trail = fake_logger.get_audit_trail("contract_5")
            self.assertEqual(len(trail), 1)
            self.assertEqual(trail[0]["action"], "redline_generation")
        finally:
            patcher.stop()


if __name__ == "__main__":
    unittest.main()
