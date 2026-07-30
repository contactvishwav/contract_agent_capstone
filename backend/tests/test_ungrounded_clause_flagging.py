"""
Regression test: LLMExtractionService already computes start_offset == -1
when an LLM-extracted clause's text can't be located anywhere in the source
contract (a signal that the model may have paraphrased or hallucinated the
clause rather than quoting it verbatim) - but nothing in the pipeline acted
on that signal. An ungrounded clause was indistinguishable from a verified
one by the time it reached violations, risk output, or the API response.

Per product decision: flag rather than silently exclude - an ungrounded
clause may still be worth a human's attention, just not full-confidence
treatment - so it stays in the pipeline but must be visibly marked
`grounded: false` everywhere clause_id already flows (P1 pattern), not
treated as equal to a grounded result.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.intelligence_tools import ClauseDetectorTool, PolicyCheckerTool, RiskCalculatorTool

_audit_logger_patcher = patch("backend.agents.intelligence_tools.AuditLogger", return_value=MagicMock())
_audit_logger_patcher.start()

# This file tests grounding-flag propagation, not caching - disable the P3-
# item-20 content-hash cache so identical SOURCE_TEXT across tests always
# exercises a real (fake) LLM call rather than returning a stale result.
_cache_disabled_patcher = patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False)
_cache_disabled_patcher.start()


class FakeLLM:
    def __init__(self, clauses):
        self._clauses = clauses

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, prompt):
        from backend.agents.llm_extraction_service import _LLMExtractionResponse, _LLMExtractedClause
        parsed = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type=t, extracted_text=text, confidence=0.9)
            for t, text in self._clauses
        ])
        return {
            "raw": SimpleNamespace(usage_metadata={"input_tokens": 20, "output_tokens": 5}),
            "parsed": parsed,
            "parsing_error": None,
        }


SOURCE_TEXT = "Payment is due Net 90 days. Governing law is California law."


class ClauseGroundingFlagTests(unittest.TestCase):
    def test_grounded_clause_marked_true(self):
        llm = FakeLLM([("Governing Law", "California law")])
        clauses = json.loads(ClauseDetectorTool(llm=llm)._run(SOURCE_TEXT, contract_id="c1"))

        self.assertEqual(len(clauses), 1)
        self.assertTrue(clauses[0]["grounded"])

    def test_ungrounded_clause_marked_false_not_silently_dropped(self):
        llm = FakeLLM([("Governing Law", "text that does not appear in the source contract at all")])
        clauses = json.loads(ClauseDetectorTool(llm=llm)._run(SOURCE_TEXT, contract_id="c1"))

        # Still present (flagged, not excluded) - a hallucination-risk clause
        # is still worth a human's attention.
        self.assertEqual(len(clauses), 1)
        self.assertFalse(clauses[0]["grounded"])
        self.assertEqual(clauses[0]["start_offset"], -1)


class ViolationAndRiskGroundingPropagationTests(unittest.TestCase):
    """
    Uses a deterministic-table category (Cap On Liability) so the
    PolicyCheckerTool assertions - about clause_grounded threading, not
    policy content - don't need an LLM mock at all. RiskCalculatorTool's
    critical_issue_details only includes CRITICAL-severity violations, so
    that half is tested with a directly-built violation dict rather than
    depending on the deterministic table's own (HIGH) severity choice.
    """

    def test_violation_carries_ungrounded_flag(self):
        clauses = [{
            "clause_id": "c1_cap_on_liability_0", "clause_type": "Cap On Liability",
            "content": "Liability shall not exceed 3 times the total fees paid.", "grounded": False,
        }]
        violations = json.loads(PolicyCheckerTool()._run(json.dumps(clauses)))["violations"]

        self.assertEqual(len(violations), 1)
        self.assertFalse(violations[0]["clause_grounded"])

    def test_grounded_violation_defaults_true(self):
        clauses = [{
            "clause_id": "c1_cap_on_liability_0", "clause_type": "Cap On Liability",
            "content": "Liability shall not exceed 3 times the total fees paid.", "grounded": True,
        }]
        violations = json.loads(PolicyCheckerTool()._run(json.dumps(clauses)))["violations"]

        self.assertTrue(violations[0]["clause_grounded"])

    def test_risk_detail_carries_ungrounded_flag(self):
        clauses = [{"clause_id": "c1_x_0", "clause_type": "Uncapped Liability", "content": "..."}]
        violations = [{
            "clause_id": "c1_x_0", "clause_type": "Uncapped Liability", "issue": "Uncapped liability",
            "severity": "CRITICAL", "clause_grounded": False,
        }]

        risk = json.loads(RiskCalculatorTool()._run(json.dumps(clauses), json.dumps(violations)))
        self.assertEqual(len(risk["critical_issue_details"]), 1)
        self.assertFalse(risk["critical_issue_details"][0]["clause_grounded"])


if __name__ == "__main__":
    unittest.main()
