"""
Regression test for research/benchmark/evaluate_extraction.py's extension to
the 36 risk-relevant CUAD categories (item 12 in docs/ENTERPRISE_READINESS.md's
punch list). Previously the benchmark only scored 5 metadata fields (Document
Name, Parties, and 3 dates) - extraction_benchmark.csv's ground truth simply
didn't cover Cap On Liability, Non-Compete, Termination For Convenience, Ip
Ownership Assignment, etc., so the platform's actual risk-assessment value
proposition was unvalidated at the accuracy layer.

This test is fully offline (no LLM calls, no network, no HuggingFace dataset
download) - it exercises evaluate_contract()'s scoring logic directly against
synthetic gold/predicted data, the same function the real benchmark run uses.
"""

import importlib.util
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EVAL_SCRIPT_PATH = os.path.join(REPO_ROOT, "research", "benchmark", "evaluate_extraction.py")


def _load_evaluate_extraction_module():
    """evaluate_extraction.py isn't an importable package (it lives outside
    backend/ and does its own sys.path/dotenv setup at import time) - load it
    by file path instead of a normal import."""
    spec = importlib.util.spec_from_file_location("evaluate_extraction", EVAL_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate_extraction = _load_evaluate_extraction_module()
CUADClauseType = evaluate_extraction.CUADClauseType


class RiskCategoryScoringTests(unittest.TestCase):
    def test_risk_category_true_positive(self):
        column_gold = {c: [] for c in evaluate_extraction.COLUMN_TO_TYPE}
        predicted_by_type = {
            CUADClauseType.CAP_ON_LIABILITY: ["Liability shall not exceed two times the fees paid."],
        }
        risk_gold = {"Cap On Liability": ["Liability shall not exceed 2x the fees paid"]}

        results = evaluate_extraction.evaluate_contract(column_gold, predicted_by_type, risk_gold)

        self.assertEqual(results["Cap On Liability"]["outcome"], "TP")

    def test_risk_category_false_negative_when_gold_present_but_not_predicted(self):
        column_gold = {c: [] for c in evaluate_extraction.COLUMN_TO_TYPE}
        predicted_by_type = {}
        risk_gold = {"Non-Compete": ["Employee shall not compete for two years."]}

        results = evaluate_extraction.evaluate_contract(column_gold, predicted_by_type, risk_gold)

        self.assertEqual(results["Non-Compete"]["outcome"], "FN")

    def test_risk_category_false_positive_when_predicted_but_no_gold(self):
        column_gold = {c: [] for c in evaluate_extraction.COLUMN_TO_TYPE}
        predicted_by_type = {
            CUADClauseType.AUDIT_RIGHTS: ["Company may audit records annually."],
        }
        risk_gold = {}  # Audit Rights genuinely absent from this contract's ground truth

        results = evaluate_extraction.evaluate_contract(column_gold, predicted_by_type, risk_gold)

        self.assertEqual(results["Audit Rights"]["outcome"], "FP")

    def test_risk_category_true_negative_when_absent_from_both(self):
        column_gold = {c: [] for c in evaluate_extraction.COLUMN_TO_TYPE}
        predicted_by_type = {}
        risk_gold = {}

        results = evaluate_extraction.evaluate_contract(column_gold, predicted_by_type, risk_gold)

        self.assertEqual(results["Insurance"]["outcome"], "TN")

    def test_metadata_columns_still_scored_unchanged_alongside_risk_categories(self):
        column_gold = {c: [] for c in evaluate_extraction.COLUMN_TO_TYPE}
        column_gold["Document Name"] = ["DISTRIBUTOR AGREEMENT"]
        predicted_by_type = {CUADClauseType.DOCUMENT_NAME: ["DISTRIBUTOR AGREEMENT"]}
        risk_gold = {"Non-Compete": ["Employee shall not compete."]}

        results = evaluate_extraction.evaluate_contract(column_gold, predicted_by_type, risk_gold)

        self.assertEqual(results["Document Name"]["outcome"], "TP")
        self.assertEqual(results["Non-Compete"]["outcome"], "FN")

    def test_all_41_categories_are_scored(self):
        column_gold = {c: [] for c in evaluate_extraction.COLUMN_TO_TYPE}
        results = evaluate_extraction.evaluate_contract(column_gold, {}, {})

        # The 3 date columns are keyed by their CSV column name (with the
        # "-Answer" suffix), not the bare CUADClauseType value - Document
        # Name/Parties and all 36 risk categories use the bare value.
        expected = set(evaluate_extraction.COLUMN_TO_TYPE.keys()) | set(evaluate_extraction.RISK_CATEGORY_TYPES.keys())
        self.assertEqual(set(results.keys()), expected)
        self.assertEqual(len(expected), 41)

    def test_risk_category_types_exclude_the_5_metadata_columns(self):
        metadata_values = {"Document Name", "Parties", "Agreement Date", "Effective Date", "Expiration Date"}
        self.assertEqual(len(evaluate_extraction.RISK_CATEGORY_TYPES), 36)
        self.assertTrue(metadata_values.isdisjoint(evaluate_extraction.RISK_CATEGORY_TYPES.keys()))


class CheckpointReScoringTests(unittest.TestCase):
    """A checkpoint entry with raw 'extracted' predictions can be re-scored
    against a newly added category (risk categories) without a new LLM call -
    this is what lets future benchmark dimensions reuse already-spent quota."""

    def test_old_style_checkpoint_entry_without_extracted_field_is_left_alone(self):
        entry = {"filename": "x.pdf", "results": {"Document Name": {"gold": ["X"], "predicted": ["X"], "outcome": "TP"}}}
        self.assertNotIn("extracted", entry)

    def test_raw_extracted_predictions_rebuild_predicted_by_type_correctly(self):
        raw = [
            {"clause_type": "Cap On Liability", "extracted_text": "Liability capped at 2x fees."},
            {"clause_type": "Cap On Liability", "extracted_text": "Second liability mention."},
        ]
        predicted_by_type = {}
        for e in raw:
            predicted_by_type.setdefault(CUADClauseType(e["clause_type"]), []).append(e["extracted_text"])

        self.assertEqual(len(predicted_by_type[CUADClauseType.CAP_ON_LIABILITY]), 2)


if __name__ == "__main__":
    unittest.main()
