"""
Phase 2 of the partial-features master-upgrade plan (showcase_readiness_
audit.md's "Risk Analysis Integrity" finding): RiskCalculatorTool used to
start every contract at the same flat risk_score = 30.0 regardless of its
declared contract_type, and never reported which factors actually
contributed to the final number (RiskDetail.tsx's UI breakdown was
independently fabricated client-side from score thresholds). This proves
the real base-by-type lookup and the itemized score_breakdown it now
returns.
"""

import json
import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.intelligence_tools import RiskCalculatorTool


class RiskCalculatorContractTypeTests(unittest.TestCase):
    def setUp(self):
        self.tool = RiskCalculatorTool()

    def _assessment(self, contract_type="general", violations=None):
        result = self.tool._run(
            clauses_json="[]",
            violations_json=json.dumps(violations or []),
            contract_id="c1",
            tenant_id="t1",
            contract_type=contract_type,
        )
        return json.loads(result)

    def test_nda_starts_lower_than_msa_with_zero_violations(self):
        nda = self._assessment(contract_type="NDA")
        msa = self._assessment(contract_type="Master Services Agreement")
        self.assertEqual(nda["overall_risk_score"], 10.0)
        self.assertEqual(msa["overall_risk_score"], 40.0)
        self.assertLess(nda["overall_risk_score"], msa["overall_risk_score"])

    def test_lookup_is_case_and_whitespace_insensitive(self):
        assessment = self._assessment(contract_type="  msa  ")
        self.assertEqual(assessment["overall_risk_score"], 40.0)

    def test_unrecognized_contract_type_keeps_original_flat_default(self):
        # Real, deliberate backward-compat guarantee: a contract_type this
        # table doesn't enumerate (or none at all) must behave exactly as
        # every contract did before this change - the flat 30.0 base.
        unspecified = self._assessment(contract_type="Joint Venture")
        no_type = self._assessment(contract_type=None)
        self.assertEqual(unspecified["overall_risk_score"], 30.0)
        self.assertEqual(no_type["overall_risk_score"], 30.0)

    def test_score_breakdown_itemizes_base_and_each_violation(self):
        assessment = self._assessment(
            contract_type="NDA",
            violations=[
                {"issue": "Missing mutual confidentiality", "severity": "CRITICAL"},
                {"issue": "Weak carve-out language", "severity": "MEDIUM"},
            ],
        )
        breakdown = assessment["score_breakdown"]
        self.assertEqual(len(breakdown), 3)  # base + 2 violations
        self.assertIn("NDA", breakdown[0]["factor"])
        self.assertEqual(breakdown[0]["points"], 10.0)
        self.assertEqual(breakdown[1]["factor"], "Missing mutual confidentiality")
        self.assertEqual(breakdown[1]["points"], 25)
        self.assertEqual(breakdown[2]["points"], 10)
        # Base (10) + CRITICAL (25) + MEDIUM (10) = 45, well under the cap.
        self.assertEqual(assessment["overall_risk_score"], 45.0)

    def test_score_breakdown_notes_the_cap_when_raw_total_exceeds_100(self):
        violations = [{"issue": f"Critical issue {i}", "severity": "CRITICAL"} for i in range(4)]
        assessment = self._assessment(contract_type="MSA", violations=violations)
        # Base 40 + 4*25 = 140, capped to 100.
        self.assertEqual(assessment["overall_risk_score"], 100.0)
        cap_entries = [e for e in assessment["score_breakdown"] if e["factor"] == "Capped at 100"]
        self.assertEqual(len(cap_entries), 1)
        self.assertEqual(cap_entries[0]["points"], -40.0)


if __name__ == "__main__":
    unittest.main()
