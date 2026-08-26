"""
Regression tests for run_quality_grader.py - the traditional path's
salvaged, decoupled A-F run-quality grade (extracted from backend/agents/
supervisor/quality_grader.py before that module and the rest of
PlanExecutionEngine were retired - see git history).

Three layers:
1. Unit tests directly against grade_run() - a pure function, no mocking
   needed, proving the rubric reads real telemetry (node_status/clauses/
   validation_result), not hardcoded values.
2. Integration tests proving _assemble_traditional_result (contract_
   intelligence_agents.py) actually calls it and threads a real,
   non-empty "quality_grade" key into the traditional path's final
   result - the real wiring point, not just the standalone function.
   These construct final_state by hand, so the *wiring* is real but the
   node_status/validation_result inputs are still hand-typed.
3. RealForcedCuadTierFailureIntegrationTests - closes the gap layer 2
   leaves open: forces a genuine degraded run through the real
   IntelligenceOrchestrator._cuad_mitigation cascade (all 3 real
   fallback tiers raising for real, same pattern as test_adaptive_
   learning_risk_wiring.py's _run_cuad_mitigation helper) rather than
   asserting against a hand-typed node_status dict, and confirms the
   resulting grade genuinely differs from a real full-success run
   through the same method.
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.agents.run_quality_grader import grade_run

FULL_SUCCESS_NODE_STATUS = {
    "clause_extraction": "success",
    "policy_checking": "success",
    "risk_calculation": "success",
    "cuad_mitigation": "success",
    "redline_generation": "success",
}

HIGH_CONFIDENCE_CLAUSES = [
    {"clause_id": "c1", "grounded": True, "confidence_score": 0.95},
    {"clause_id": "c2", "grounded": True, "confidence_score": 0.92},
]


class GradeRunPureFunctionTests(unittest.TestCase):
    def test_full_success_high_confidence_no_validation_signal_grades_a(self):
        result = grade_run(FULL_SUCCESS_NODE_STATUS, True, HIGH_CONFIDENCE_CLAUSES, validation_result=None)
        self.assertEqual(result["grade"], "A")
        self.assertEqual(result["grounded_rate"], 1.0)
        self.assertGreaterEqual(result["avg_confidence"], 0.9)

    def test_full_success_with_high_cuad_validation_confidence_grades_a(self):
        validation_result = MagicMock(is_valid=True, confidence_score=0.9)
        result = grade_run(FULL_SUCCESS_NODE_STATUS, True, HIGH_CONFIDENCE_CLAUSES, validation_result)
        self.assertEqual(result["grade"], "A")
        self.assertEqual(result["validation_confidence"], 0.9)

    def test_low_cuad_validation_confidence_caps_grade_at_b_even_with_perfect_clauses(self):
        """Real scenario observed live this session: cuad_mitigation
        reports "success" (it ran, produced a result) but its own
        validator flagged low confidence (0.28, is_valid varies) - a
        signal node_status alone can't see. Proves the grader reads
        CUAD Mitigation's real validation output, not just node_status."""
        validation_result = MagicMock(is_valid=True, confidence_score=0.28)
        result = grade_run(FULL_SUCCESS_NODE_STATUS, True, HIGH_CONFIDENCE_CLAUSES, validation_result)
        self.assertEqual(result["grade"], "B")
        self.assertEqual(result["validation_confidence"], 0.28)

    def test_cuad_validator_flagging_is_valid_false_demotes_to_c(self):
        """A stronger signal than merely low confidence: the validator
        found real structural problems (missing fields, inconsistent risk
        scores) in an otherwise "successful" cuad_mitigation run."""
        validation_result = MagicMock(is_valid=False, confidence_score=0.6)
        result = grade_run(FULL_SUCCESS_NODE_STATUS, True, HIGH_CONFIDENCE_CLAUSES, validation_result)
        self.assertEqual(result["grade"], "C")
        self.assertIn("validator flagged", " ".join(result["reasons"]))

    def test_core_step_failure_grades_f_regardless_of_everything_else(self):
        node_status = {**FULL_SUCCESS_NODE_STATUS, "risk_calculation": "error"}
        result = grade_run(node_status, False, HIGH_CONFIDENCE_CLAUSES, validation_result=None)
        self.assertEqual(result["grade"], "F")

    def test_non_core_node_failure_cuad_mitigation_error_demotes_to_c(self):
        """Real degradation this session's own CUAD-mitigation cascade
        can produce: all 3 fallback tiers exhausted -> node_status
        "error", but clause_extraction/policy_checking/risk_calculation
        (the core trio) still succeeded - not an automatic F."""
        node_status = {**FULL_SUCCESS_NODE_STATUS, "cuad_mitigation": "error"}
        result = grade_run(node_status, True, HIGH_CONFIDENCE_CLAUSES, validation_result=None)
        self.assertEqual(result["grade"], "C")

    def test_policy_checking_partial_demotes_to_c(self):
        """The traditional path's one node that genuinely supports a
        3rd state (PolicyCheckerTool._run) - not just success/error."""
        node_status = {**FULL_SUCCESS_NODE_STATUS, "policy_checking": "partial"}
        result = grade_run(node_status, True, HIGH_CONFIDENCE_CLAUSES, validation_result=None)
        self.assertEqual(result["grade"], "C")

    def test_low_grounded_rate_grades_f_even_with_all_nodes_successful(self):
        ungrounded_clauses = [
            {"clause_id": "c1", "grounded": False, "confidence_score": 0.9},
            {"clause_id": "c2", "grounded": False, "confidence_score": 0.9},
            {"clause_id": "c3", "grounded": True, "confidence_score": 0.9},
        ]
        result = grade_run(FULL_SUCCESS_NODE_STATUS, True, ungrounded_clauses, validation_result=None)
        self.assertEqual(result["grade"], "F")

    def test_no_clauses_defaults_grounded_rate_and_confidence_to_1_not_zero(self):
        """Absence of data (e.g. a genuinely clause-free contract) must
        not be scored as if it were a grounding/confidence failure."""
        result = grade_run(FULL_SUCCESS_NODE_STATUS, True, [], validation_result=None)
        self.assertEqual(result["grounded_rate"], 1.0)
        self.assertEqual(result["avg_confidence"], 1.0)
        self.assertEqual(result["grade"], "A")


class TraditionalPathWiringTests(unittest.TestCase):
    """Proves the real integration point - _assemble_traditional_result -
    actually calls grade_run and threads a real result into the final
    dict, not just that the standalone function works in isolation."""

    def test_assemble_traditional_result_includes_real_quality_grade(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator

        final_state = {
            "is_complete": True,
            "extracted_clauses": HIGH_CONFIDENCE_CLAUSES,
            "policy_violations": [],
            "risk_data": {"overall_risk_score": 20.0, "risk_level": "LOW"},
            "redline_suggestions": [],
            "cuad_deviations": [],
            "jurisdiction_info": {},
            "precedent_matches": [],
            "validation_result": MagicMock(is_valid=True, confidence_score=0.95),
            "node_status": dict(FULL_SUCCESS_NODE_STATUS),
        }

        result = IntelligenceOrchestrator._assemble_traditional_result(final_state, "langgraph_traditional_explicit")

        self.assertIn("quality_grade", result)
        self.assertEqual(result["quality_grade"]["grade"], "A")

    def test_assemble_traditional_result_reflects_node_level_degradation_not_hardcoded(self):
        """Same method, only the input telemetry changes - proves the
        wired-in grade is computed from this specific run's real data,
        not a constant. _assemble_traditional_result's own pre-existing
        logic (unrelated to this grader) already ties processing_complete
        to "no error/partial anywhere in node_status" - so a node-level
        failure here correctly reaches grade_run's "not processing_
        complete" branch (D), not the separate "degraded" branch (C)."""
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator

        degraded_node_status = {**FULL_SUCCESS_NODE_STATUS, "cuad_mitigation": "error"}
        final_state = {
            "is_complete": True,
            "extracted_clauses": HIGH_CONFIDENCE_CLAUSES,
            "policy_violations": [],
            "risk_data": {"overall_risk_score": 20.0, "risk_level": "LOW"},
            "redline_suggestions": [],
            "cuad_deviations": [],
            "jurisdiction_info": {},
            "precedent_matches": [],
            "validation_result": None,
            "node_status": degraded_node_status,
        }

        result = IntelligenceOrchestrator._assemble_traditional_result(final_state, "langgraph_traditional_explicit")

        self.assertEqual(result["quality_grade"]["grade"], "D")
        self.assertNotEqual(result["quality_grade"]["grade"], "A")

    def test_assemble_traditional_result_reflects_cuad_validation_flag_alone_as_c(self):
        """The one real path to a C grade through this exact wiring: node_
        status stays fully clean (cuad_mitigation genuinely succeeded) but
        its own validator flagged the result - processing_complete stays
        True, so grade_run's separate "degraded" branch (not the
        processing_complete one) is what's actually exercised here."""
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator

        final_state = {
            "is_complete": True,
            "extracted_clauses": HIGH_CONFIDENCE_CLAUSES,
            "policy_violations": [],
            "risk_data": {"overall_risk_score": 20.0, "risk_level": "LOW"},
            "redline_suggestions": [],
            "cuad_deviations": [],
            "jurisdiction_info": {},
            "precedent_matches": [],
            "validation_result": MagicMock(is_valid=False, confidence_score=0.5),
            "node_status": dict(FULL_SUCCESS_NODE_STATUS),
        }

        result = IntelligenceOrchestrator._assemble_traditional_result(final_state, "langgraph_traditional_explicit")

        self.assertEqual(result["quality_grade"]["grade"], "C")


class RealForcedCuadTierFailureIntegrationTests(unittest.TestCase):
    """Forces a genuine degraded run through the real CUAD Mitigation
    cascade (contract_intelligence_agents.py's _cuad_mitigation ->
    _cuad_mitigation_fallback_enhanced -> _cuad_mitigation_fallback),
    then feeds the real resulting state into _assemble_traditional_result
    - proving the wired grade reflects a run that genuinely degraded,
    not a hand-typed node_status dict asserting the wiring works in the
    abstract. Same bypass-constructor + tool-mocking pattern as test_
    adaptive_learning_risk_wiring.py's _run_cuad_mitigation helper."""

    def _run_full_pipeline(self, deviation_tool_raises: bool):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator

        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None
        state = {
            "extracted_clauses": HIGH_CONFIDENCE_CLAUSES,
            "policy_violations": [],
            "contract_text": "Sample contract text.",
            "contract_id": "contract_forced_tier_failure",
            "tenant_id": "tenant_1",
            "risk_data": {"overall_risk_score": 20.0, "risk_level": "LOW"},
            # The 4 other real nodes already ran successfully earlier in
            # the graph - only cuad_mitigation's outcome is under test.
            "node_status": {
                "clause_extraction": "success",
                "policy_checking": "success",
                "risk_calculation": "success",
                "redline_generation": "success",
            },
        }

        deviation_side_effect = RuntimeError("Deviation detection backend unavailable") if deviation_tool_raises else None

        with patch("backend.agents.optimized_cuad_tools.OptimizedDeviationDetectorTool") as MockOptDev, \
             patch("backend.agents.optimized_cuad_tools.OptimizedJurisdictionAdapterTool") as MockOptJur, \
             patch("backend.agents.optimized_cuad_tools.OptimizedPrecedentMatcherTool") as MockOptPrec, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedDeviationDetectorTool") as MockEnhDev, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedJurisdictionAdapterTool") as MockEnhJur, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedPrecedentMatcherTool") as MockEnhPrec, \
             patch("backend.agents.cuad_mitigation_tools.DeviationDetectorTool") as MockBaseDev, \
             patch("backend.agents.cuad_mitigation_tools.JurisdictionAdapterTool") as MockBaseJur, \
             patch("backend.agents.cuad_mitigation_tools.PrecedentMatcherTool") as MockBasePrec, \
             patch("backend.validation.cuad_validator.validate_cuad_analysis") as mock_validate, \
             patch("backend.infrastructure.audit_logger.AuditLogger.log_event"):

            for mock_dev in (MockOptDev, MockEnhDev, MockBaseDev):
                if deviation_side_effect is not None:
                    mock_dev.return_value._run.side_effect = deviation_side_effect
                else:
                    mock_dev.return_value._run.return_value = "[]"
            for mock_jur in (MockOptJur, MockEnhJur, MockBaseJur):
                mock_jur.return_value._run.return_value = '{"jurisdiction": "unknown", "industry": "general"}'
            for mock_prec in (MockOptPrec, MockEnhPrec, MockBasePrec):
                mock_prec.return_value._run.return_value = "[]"
            mock_validate.return_value = MagicMock(is_valid=True, confidence_score=0.9)

            cuad_result_state = orchestrator._cuad_mitigation(state)

        final_state = {
            "is_complete": True,
            "extracted_clauses": cuad_result_state["extracted_clauses"],
            "policy_violations": cuad_result_state["policy_violations"],
            "risk_data": cuad_result_state["risk_data"],
            "redline_suggestions": [],
            "cuad_deviations": cuad_result_state.get("cuad_deviations", []),
            "jurisdiction_info": cuad_result_state.get("jurisdiction_info", {}),
            "precedent_matches": cuad_result_state.get("precedent_matches", []),
            "validation_result": cuad_result_state.get("validation_result"),
            "node_status": cuad_result_state["node_status"],
        }
        return IntelligenceOrchestrator._assemble_traditional_result(final_state, "langgraph_traditional_explicit")

    def test_real_full_success_run_through_all_three_tiers_grades_high(self):
        """Baseline: Phase 3 tools genuinely succeed (no mocked failure
        anywhere in the cascade) - real node_status ends up "success"."""
        result = self._run_full_pipeline(deviation_tool_raises=False)

        self.assertEqual(result["node_status"]["cuad_mitigation"], "success")
        self.assertTrue(result["processing_complete"])
        self.assertEqual(result["quality_grade"]["grade"], "A")

    def test_real_forced_exhaustion_of_all_three_cuad_tiers_grades_lower_than_baseline(self):
        """Degraded case: the deviation-detector tool genuinely raises on
        all 3 real tiers (Phase 3 -> Phase 2 -> Phase 1), so the real
        try/except cascade in _cuad_mitigation/_cuad_mitigation_fallback_
        enhanced/_cuad_mitigation_fallback actually executes end to end,
        landing on the real final except block's node_status["cuad_
        mitigation"] = "error" - not a value this test types in."""
        result = self._run_full_pipeline(deviation_tool_raises=True)

        self.assertEqual(result["node_status"]["cuad_mitigation"], "error")
        self.assertFalse(result["processing_complete"])
        self.assertEqual(result["quality_grade"]["grade"], "D")

        baseline = self._run_full_pipeline(deviation_tool_raises=False)
        self.assertNotEqual(result["quality_grade"]["grade"], baseline["quality_grade"]["grade"])


if __name__ == "__main__":
    unittest.main()
