"""
Regression tests for a real, confirmed gap found during an earlier live
pipeline audit: cuad_mitigation genuinely runs real work on every real
analysis (deviation detection, jurisdiction adaptation, precedent
matching - confirmed live via the real audit trail and node_status of a
production run) but had zero AuditLogger entries and no node_status key
on any of its exit paths - unlike clause_extraction/policy_checking/
risk_calculation/redline_generation right next to it in the same graph.
Confirmed live: GET /api/audit/trail/{contract_id} for a real analysis
showed exactly 4 events, never "cuad_mitigation", and node_status was
missing the key entirely even though the step visibly ran.

This tests IntelligenceOrchestrator._cuad_mitigation (the default,
use_planning=False LangGraph path) across all 3 real fallback tiers
(Phase 3 optimized, Phase 2, Phase 1) plus the final all-tiers-failed
error path, proving each now produces a real, queryable audit entry and
a real node_status["cuad_mitigation"] value - without changing what any
tier actually computes.

Also covers a follow-up product decision: whether "Validate Results"
(validate_cuad_analysis, called inline inside the Phase 3 tier only)
should be its own distinct tracked stage. Decision: no - it has no
separate LangGraph node and no real standalone duration, and only 1 of
3 tiers ever calls it, so it stays a qualifier on cuad_mitigation's own
audit entry (already-present "validated"/"confidence_score" fields on
Phase 3) rather than a fake 6th stage with placeholder timing. What
these tests add: Phase 2/Phase 1, which never call the validator,
must explicitly declare "validated": None / "confidence_score": None
in their audit metadata (schema consistency, not new logic) - and the
Phase 1 *success* branch is now exercised for the first time (the
existing suite only ever hit its failure branch).
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator


def _make_orchestrator():
    orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
    orchestrator.llm = None  # bypassing __init__, not needed for this method
    return orchestrator


def _base_state(**overrides):
    state = {
        "contract_text": "Sample contract text for CUAD mitigation.",
        "contract_id": "UPLOADED_TEST_CUAD",
        "tenant_id": "tenant_a",
        "extracted_clauses": [{"clause_id": "c1", "clause_type": "Liability", "risk_level": "LOW"}],
        "policy_violations": [],
        "risk_data": {},
        "node_status": {"clause_extraction": "success", "policy_checking": "success", "risk_calculation": "success"},
    }
    state.update(overrides)
    return state


class CuadMitigationPhase3ObservabilityTests(unittest.TestCase):
    """The default, real tier - OptimizedDeviationDetectorTool et al."""

    def test_success_produces_audit_entry_and_node_status(self):
        orchestrator = _make_orchestrator()
        state = _base_state()

        with patch("backend.agents.optimized_cuad_tools.OptimizedDeviationDetectorTool") as MockDev, \
             patch("backend.agents.optimized_cuad_tools.OptimizedJurisdictionAdapterTool") as MockJur, \
             patch("backend.agents.optimized_cuad_tools.OptimizedPrecedentMatcherTool") as MockPrec, \
             patch("backend.agents.feedback_learning_system.AdaptiveAnalyzer") as MockAdaptive, \
             patch("backend.validation.cuad_validator.validate_cuad_analysis") as mock_validate, \
             patch("backend.infrastructure.audit_logger.AuditLogger.log_event") as mock_log_event:
            MockDev.return_value._run.return_value = "[]"
            MockJur.return_value._run.return_value = '{"jurisdiction": "US-CA", "industry": "tech", "risk_factors": []}'
            MockPrec.return_value._run.return_value = "[]"
            MockAdaptive.return_value.enhance_analysis.side_effect = lambda c, b: b
            mock_validate.return_value = MagicMock(is_valid=True, confidence_score=0.87)

            result = orchestrator._cuad_mitigation(state)

        self.assertEqual(result["node_status"]["cuad_mitigation"], "success")
        # Every other real node_status entry survives untouched.
        self.assertEqual(result["node_status"]["clause_extraction"], "success")

        mock_log_event.assert_called_once()
        _, kwargs = mock_log_event.call_args
        self.assertEqual(kwargs["action"], "cuad_mitigation")
        self.assertEqual(kwargs["status"], "success")
        self.assertEqual(kwargs["resource_id"], "UPLOADED_TEST_CUAD")
        self.assertEqual(kwargs["tenant_id"], "tenant_a")
        self.assertEqual(kwargs["metadata"]["tier"], "optimized")
        self.assertEqual(kwargs["metadata"]["jurisdiction"], "US-CA")
        self.assertTrue(kwargs["metadata"]["validated"])


class CuadMitigationFallbackTierObservabilityTests(unittest.TestCase):
    """Phase 3 fails -> Phase 2 (enhanced) succeeds - the audit entry and
    node_status must still land, tagged with the tier that actually ran."""

    def test_phase2_fallback_success_produces_audit_entry_and_node_status(self):
        orchestrator = _make_orchestrator()
        state = _base_state()

        with patch("backend.agents.optimized_cuad_tools.OptimizedDeviationDetectorTool") as MockDev, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedDeviationDetectorTool") as MockEnhDev, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedJurisdictionAdapterTool") as MockEnhJur, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedPrecedentMatcherTool") as MockEnhPrec, \
             patch("backend.infrastructure.audit_logger.AuditLogger.log_event") as mock_log_event:
            MockDev.return_value._run.side_effect = RuntimeError("Phase 3 unavailable")
            MockEnhDev.return_value._run.return_value = "[]"
            MockEnhJur.return_value._run.return_value = '{"jurisdiction": "unknown", "industry": "general"}'
            MockEnhPrec.return_value._run.return_value = "[]"

            result = orchestrator._cuad_mitigation(state)

        self.assertEqual(result["node_status"]["cuad_mitigation"], "success")
        mock_log_event.assert_called_once()
        _, kwargs = mock_log_event.call_args
        self.assertEqual(kwargs["action"], "cuad_mitigation")
        self.assertEqual(kwargs["status"], "success")
        self.assertEqual(kwargs["metadata"]["tier"], "phase2_fallback")
        # validate_cuad_analysis only runs in the Phase 3 tier - Phase 2's
        # audit entry must explicitly declare "not applicable" rather than
        # silently omitting the keys, so a consumer can tell this apart
        # from "validated and found nothing wrong".
        self.assertIsNone(kwargs["metadata"]["validated"])
        self.assertIsNone(kwargs["metadata"]["confidence_score"])

    def test_phase1_fallback_success_produces_audit_entry_and_node_status(self):
        """Real gap this closes: the Phase 1 fallback's *success* branch
        (Phase 3 and Phase 2 both fail, Phase 1 succeeds) was never
        exercised by the existing suite - only its failure branch was
        covered via test_all_tiers_failing below. Same schema-consistency
        requirement as Phase 2: validated/confidence_score must be
        explicit None, not silently absent."""
        orchestrator = _make_orchestrator()
        state = _base_state()

        with patch("backend.agents.optimized_cuad_tools.OptimizedDeviationDetectorTool") as MockDev, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedDeviationDetectorTool") as MockEnhDev, \
             patch("backend.agents.cuad_mitigation_tools.DeviationDetectorTool") as MockBasicDev, \
             patch("backend.agents.cuad_mitigation_tools.JurisdictionAdapterTool") as MockBasicJur, \
             patch("backend.agents.cuad_mitigation_tools.PrecedentMatcherTool") as MockBasicPrec, \
             patch("backend.infrastructure.audit_logger.AuditLogger.log_event") as mock_log_event:
            MockDev.return_value._run.side_effect = RuntimeError("Phase 3 down")
            MockEnhDev.return_value._run.side_effect = RuntimeError("Phase 2 down")
            MockBasicDev.return_value._run.return_value = "[]"
            MockBasicJur.return_value._run.return_value = '{"jurisdiction": "unknown", "industry": "general"}'
            MockBasicPrec.return_value._run.return_value = "[]"

            result = orchestrator._cuad_mitigation(state)

        self.assertEqual(result["node_status"]["cuad_mitigation"], "success")
        mock_log_event.assert_called_once()
        _, kwargs = mock_log_event.call_args
        self.assertEqual(kwargs["action"], "cuad_mitigation")
        self.assertEqual(kwargs["status"], "success")
        self.assertEqual(kwargs["metadata"]["tier"], "phase1_fallback")
        self.assertIsNone(kwargs["metadata"]["validated"])
        self.assertIsNone(kwargs["metadata"]["confidence_score"])

    def test_all_tiers_failing_produces_error_audit_entry_and_error_node_status(self):
        """Real bug this closes: before this fix, a total failure across
        all 3 tiers left node_status silently missing the key entirely -
        indistinguishable from "never ran" instead of "ran and failed"."""
        orchestrator = _make_orchestrator()
        state = _base_state()

        with patch("backend.agents.optimized_cuad_tools.OptimizedDeviationDetectorTool") as MockDev, \
             patch("backend.agents.enhanced_cuad_tools.EnhancedDeviationDetectorTool") as MockEnhDev, \
             patch("backend.agents.cuad_mitigation_tools.DeviationDetectorTool") as MockBasicDev, \
             patch("backend.infrastructure.audit_logger.AuditLogger.log_event") as mock_log_event:
            MockDev.return_value._run.side_effect = RuntimeError("Phase 3 down")
            MockEnhDev.return_value._run.side_effect = RuntimeError("Phase 2 down")
            MockBasicDev.return_value._run.side_effect = RuntimeError("Phase 1 down too")

            result = orchestrator._cuad_mitigation(state)

        self.assertEqual(result["node_status"]["cuad_mitigation"], "error")
        mock_log_event.assert_called_once()
        _, kwargs = mock_log_event.call_args
        self.assertEqual(kwargs["action"], "cuad_mitigation")
        self.assertEqual(kwargs["status"], "failure")
        self.assertIn("Phase 1 down too", kwargs["error_details"])


if __name__ == "__main__":
    unittest.main()
