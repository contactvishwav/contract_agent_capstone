"""
Regression test: extracted clauses had no stable identifier, so
PolicyCheckerTool's violations and RiskCalculatorTool's critical-issue
output could only reference a clause by its category string (e.g.
"Payment Terms") - not the exact clause instance that triggered a
violation/risk factor. This matters when a contract has multiple clauses of
the same type.

The id must be deterministic (same clause -> same id across re-runs of the
same contract), not a random uuid, since violations/risk detail need to
point back to a stable clause reference on re-analysis.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.intelligence_tools import ClauseDetectorTool, PolicyCheckerTool, RiskCalculatorTool

# This test file only cares about clause_id threading, not audit logging
# (covered separately by test_intelligence_tool_audit_logging.py) - patch
# AuditLogger out entirely so these tests don't depend on whether some
# earlier test in the session happened to leave a mocked Neo4j connection
# cached in sys.modules.
_audit_logger_patcher = patch("backend.agents.intelligence_tools.AuditLogger", return_value=MagicMock())
_audit_logger_patcher.start()


class FakeLLM:
    def __init__(self, clauses):
        self._clauses = clauses

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        from backend.agents.llm_extraction_service import _LLMExtractionResponse, _LLMExtractedClause
        return _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type=t, extracted_text=text, confidence=0.9)
            for t, text in self._clauses
        ])


TEXT = "Payment is due Net 90 days. Governing law is California law."


class ClauseIdStabilityTests(unittest.TestCase):
    def test_clause_id_is_non_empty_and_stable_across_runs(self):
        llm = FakeLLM([("Governing Law", "California law.")])
        tool = ClauseDetectorTool(llm=llm)

        run1 = json.loads(tool._run(TEXT, contract_id="contract_1"))
        run2 = json.loads(tool._run(TEXT, contract_id="contract_1"))

        self.assertTrue(run1[0]["clause_id"])
        self.assertEqual(run1[0]["clause_id"], run2[0]["clause_id"])

    def test_duplicate_clause_type_at_same_offset_gets_distinct_ids(self):
        # Two clauses of the same type that both fail span resolution
        # (start_offset == -1) collide on the base id and must be
        # disambiguated with a _dup suffix.
        llm = FakeLLM([
            ("Governing Law", "text not present in source"),
            ("Governing Law", "also not present in source"),
        ])
        tool = ClauseDetectorTool(llm=llm)

        clauses = json.loads(tool._run(TEXT, contract_id="contract_1"))

        self.assertEqual(len(clauses), 2)
        self.assertNotEqual(clauses[0]["clause_id"], clauses[1]["clause_id"])
        self.assertTrue(clauses[1]["clause_id"].endswith("_dup1"))


class ViolationClauseIdTests(unittest.TestCase):
    def test_violation_carries_triggering_clauses_id(self):
        # Cap On Liability is in the deterministic table (P2 item 2), so
        # this exercises PolicyCheckerTool's real clause_id threading
        # without needing an LLM mock.
        clauses = [{
            "clause_id": "c1_cap_on_liability_0", "clause_type": "Cap On Liability",
            "content": "Liability shall not exceed 3 times the total fees paid.",
        }]
        violations = json.loads(PolicyCheckerTool()._run(json.dumps(clauses)))

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["clause_id"], "c1_cap_on_liability_0")


class RiskDetailClauseIdTests(unittest.TestCase):
    def test_critical_issue_details_carry_clause_id_and_critical_issues_unchanged(self):
        # RiskCalculatorTool's critical_issue_details only includes
        # CRITICAL-severity violations - built directly here rather than
        # via PolicyCheckerTool, whose deterministic-table categories are
        # HIGH/MEDIUM, to isolate what this test actually checks: clause_id
        # threading through RiskCalculatorTool, not PolicyCheckerTool's own
        # severity assignment.
        clauses = [{"clause_id": "c1_uncapped_liability_0", "clause_type": "Uncapped Liability", "content": "..."}]
        violations = [{
            "clause_id": "c1_uncapped_liability_0", "clause_type": "Uncapped Liability",
            "issue": "Liability is uncapped", "severity": "CRITICAL",
        }]

        risk = json.loads(RiskCalculatorTool()._run(json.dumps(clauses), json.dumps(violations)))

        # Frontend compatibility guard: critical_issues stays a plain string list.
        self.assertEqual(risk["critical_issues"], [violations[0]["issue"]])
        self.assertTrue(all(isinstance(i, str) for i in risk["critical_issues"]))

        # New, additive field carries the clause reference.
        self.assertEqual(len(risk["critical_issue_details"]), 1)
        self.assertEqual(risk["critical_issue_details"][0]["clause_id"], "c1_uncapped_liability_0")
        self.assertEqual(risk["critical_issue_details"][0]["issue"], violations[0]["issue"])


if __name__ == "__main__":
    unittest.main()
