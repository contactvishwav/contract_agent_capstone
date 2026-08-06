"""
Tests for the real A-F quality grading rubric (backend/agents/supervisor/
quality_grader.py) - see that module's docstring for the full rationale
and the exact rule ordering. Every test constructs the same shape
PlanExecutionEngine._format_final_results already produces, so these
tests exercise the rubric exactly as real callers will.
"""

import unittest
from unittest.mock import patch

from backend.agents.supervisor.quality_grader import grade_analysis


def _clause(grounded=True, confidence=0.9):
    return {"grounded": grounded, "confidence_score": confidence}


class QualityGraderTests(unittest.TestCase):
    def test_core_step_failure_is_an_f_regardless_of_everything_else(self):
        result = {
            "node_status": {"extract_clauses": "failed", "check_policies": "success", "assess_risk": "success"},
            "processing_complete": False,
            "clauses": [_clause(), _clause()],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "F")

    def test_low_grounded_rate_is_an_f_even_if_every_step_succeeded(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "success", "assess_risk": "success"},
            "processing_complete": True,
            "clauses": [_clause(grounded=False), _clause(grounded=False), _clause(grounded=True)],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "F")
        self.assertLess(graded["grounded_rate"], 0.5)

    def test_incomplete_non_core_step_with_ok_grounding_is_a_d(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "success", "assess_risk": "success", "generate_redlines": "failed"},
            "processing_complete": False,
            "clauses": [_clause(), _clause()],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "D")

    def test_partial_step_with_low_grounding_is_a_d(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "partial", "assess_risk": "success"},
            "processing_complete": True,
            "clauses": [_clause(grounded=False), _clause(grounded=True), _clause(grounded=True)],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "D")

    def test_partial_step_with_decent_grounding_is_a_c(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "partial", "assess_risk": "success"},
            "processing_complete": True,
            "clauses": [_clause(grounded=True), _clause(grounded=True), _clause(grounded=True)],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "C")

    def test_all_success_but_middling_grounding_is_a_b(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "success", "assess_risk": "success"},
            "processing_complete": True,
            "clauses": [_clause(grounded=True), _clause(grounded=True), _clause(grounded=False)],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "B")

    def test_all_success_but_low_confidence_is_a_b(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "success", "assess_risk": "success"},
            "processing_complete": True,
            "clauses": [_clause(grounded=True, confidence=0.5), _clause(grounded=True, confidence=0.6)],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "B")

    def test_all_success_high_grounding_high_confidence_is_an_a(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "success", "assess_risk": "success"},
            "processing_complete": True,
            "clauses": [_clause(grounded=True, confidence=0.95), _clause(grounded=True, confidence=0.9)],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "A")

    def test_no_clauses_at_all_does_not_crash_and_grounded_rate_defaults_neutral(self):
        result = {
            "node_status": {"extract_clauses": "success", "check_policies": "success", "assess_risk": "success"},
            "processing_complete": True,
            "clauses": [],
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "A")
        self.assertEqual(graded["grounded_rate"], 1.0)

    def test_error_result_shape_grades_as_f(self):
        # Matches PlanExecutionEngine._format_error_results's shape.
        result = {
            "clauses": [], "violations": [], "risk_assessment": {}, "redlines": [],
            "node_status": {"extract_clauses": "failed"},
            "processing_complete": False,
        }
        graded = grade_analysis(result)
        self.assertEqual(graded["grade"], "F")

    def test_grade_result_includes_human_readable_reasons(self):
        result = {
            "node_status": {"extract_clauses": "failed"},
            "processing_complete": False,
            "clauses": [],
        }
        graded = grade_analysis(result)
        self.assertTrue(graded["reasons"])
        self.assertIsInstance(graded["reasons"][0], str)


class PlanExecutionEngineGradingWiringTests(unittest.TestCase):
    """Proves quality_grade is actually present in what PlanExecutionEngine
    itself returns, not just testable as a standalone pure function."""

    def test_format_final_results_includes_a_real_quality_grade(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.planning.execution_engine import PlanExecutionEngine

        engine = PlanExecutionEngine()
        engine.execution_context = {
            "extracted_clauses": [_clause(grounded=True, confidence=0.95)],
            "policy_violations": [],
            "risk_data": {"overall_risk_score": 10.0, "risk_level": "LOW"},
        }
        result = engine._format_final_results({
            "extract_clauses": "success", "check_policies": "success", "assess_risk": "success",
        })

        self.assertIn("quality_grade", result)
        self.assertEqual(result["quality_grade"]["grade"], "A")

    def test_format_error_results_includes_a_real_quality_grade(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.planning.execution_engine import PlanExecutionEngine

        engine = PlanExecutionEngine()
        result = engine._format_error_results("boom", {"extract_clauses": "failed"})

        self.assertIn("quality_grade", result)
        self.assertEqual(result["quality_grade"]["grade"], "F")


if __name__ == "__main__":
    unittest.main()
