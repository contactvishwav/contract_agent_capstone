"""
Wires the existing feedback/pattern-learning system into actual risk
scoring, closing three breaks confirmed during verification (none of
which a mocked-per-component suite had caught, since each piece worked
"correctly" in isolation):

1. The default LangGraph path's IntelligenceOrchestrator._cuad_mitigation
   computes enhanced_clauses via AdaptiveAnalyzer.enhance_analysis for
   real, on every real analysis - originally verified end-to-end through
   PlanExecutionEngine's now-retired equivalent (_execute_cuad_mitigation
   + _update_context_with_result), which had an identical break at the
   time (the enhanced_clauses result was computed but discarded). Now
   exercised directly against the real, sole surviving path.

2. AdaptiveAnalyzer._apply_risk_pattern wrote its result under a sibling
   "learned_risk_adjustment" key instead of the "risk_level" key every
   consumer actually reads - a matched pattern never changed anything a
   client could see.

3. domain/entities.py's ContractClause had no field for any of this, so
   even if 1 and 2 were fixed, _convert_to_domain_entities would have
   silently dropped it at the domain-entity boundary.

Also closes the incidental finding that made #2 undemonstrable in the
first place: ClauseDetectorTool never set risk_level on a clause at all,
so every clause's risk_level was unconditionally "LOW" via
_convert_to_domain_entities's default fallback - there was no real
baseline for a learned pattern to override. Fixed via
feedback_learning_system.compute_baseline_risk_level: primarily derived
from the max severity of any policy violations already found for that
clause (_check_policies always runs before _cuad_mitigation in the real
graph), falling back to a clause-type inherent-risk category (Uncapped
Liability, Non-Compete, etc.) when no violation matches.

The end-to-end test below proves the real chain - feedback submission
via FeedbackCollector.collect_decision -> PatternLearner.learn_from_
decisions reading it back -> AdaptiveAnalyzer applying it -> the final
clause risk_level actually changing - rather than each piece mocked and
tested alone, which is exactly the kind of gap that missed this the
first time.
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.feedback_learning_system import (
        compute_baseline_risk_level, FeedbackCollector, LegalDecision,
    )
    from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator


class BaselineRiskLevelTests(unittest.TestCase):
    """compute_baseline_risk_level must never fall back to a silent
    constant - every clause gets a real, non-constant value."""

    def test_uses_matching_violation_severity_when_present(self):
        clause = {"clause_id": "c1", "clause_type": "Renewal Term"}  # inherently LOW, would be MEDIUM/LOW by type
        violations = [
            {"clause_id": "c1", "severity": "CRITICAL"},
            {"clause_id": "other_clause", "severity": "LOW"},  # must not leak into c1's result
        ]
        self.assertEqual(compute_baseline_risk_level(clause, violations), "CRITICAL")

    def test_takes_max_severity_among_multiple_matching_violations(self):
        clause = {"clause_id": "c1", "clause_type": "Governing Law"}
        violations = [
            {"clause_id": "c1", "severity": "LOW"},
            {"clause_id": "c1", "severity": "HIGH"},
        ]
        self.assertEqual(compute_baseline_risk_level(clause, violations), "HIGH")

    def test_falls_back_to_inherent_high_risk_clause_type_when_no_violation(self):
        clause = {"clause_id": "c1", "clause_type": "Uncapped Liability"}
        self.assertEqual(compute_baseline_risk_level(clause, violations=[]), "HIGH")

    def test_falls_back_to_inherent_low_risk_clause_type_when_no_violation(self):
        clause = {"clause_id": "c1", "clause_type": "Governing Law"}
        self.assertEqual(compute_baseline_risk_level(clause, violations=[]), "LOW")

    def test_unlisted_clause_type_with_no_violation_defaults_to_medium_not_low(self):
        """The specific regression this whole fix targets: a clause with no
        matching violation and no special-cased type must NOT silently
        collapse to "LOW" (the old default) - it should land on the
        genuinely-uncertain MEDIUM tier instead."""
        clause = {"clause_id": "c1", "clause_type": "Some Unmapped Clause Type"}
        result = compute_baseline_risk_level(clause, violations=[])
        self.assertEqual(result, "MEDIUM")
        self.assertNotEqual(result, "LOW")


class FakeDecisionGraph:
    """Minimal in-memory stand-in for the two Cypher shapes FeedbackCollector
    uses (MERGE write / MATCH read of :LegalDecision nodes) - real enough
    that PatternLearner.learn_from_decisions genuinely round-trips through
    it, not a pre-canned pattern handed to AdaptiveAnalyzer directly."""

    def __init__(self):
        self.decisions = {}

    def query(self, cypher: str, params: dict = None):
        params = params or {}
        if "MERGE (d:LegalDecision" in cypher:
            self.decisions[params["decision_id"]] = dict(params)
            return [{"d": dict(params)}]
        if "MATCH (d:LegalDecision)" in cypher:
            matches = [d for d in self.decisions.values() if d["clause_type"] == params["clause_type"]]
            return [{"d": d} for d in matches[:params.get("limit", 50)]]
        return []


def _submit_decision(graph, i, clause_type, original_risk, override_risk):
    with patch("backend.infrastructure.contract_repository.graph", graph):
        collector = FeedbackCollector()
    collector.repository.graph = graph
    collector.collect_decision(LegalDecision(
        decision_id=f"decision_{i}",
        contract_id="contract_1",
        clause_id=f"c_{i}",
        clause_type=clause_type,
        original_analysis={"risk_level": original_risk},
        legal_decision="modified",
        legal_feedback="risk was understated",
        risk_assessment_override=override_risk,
        confidence_score=0.9,
    ))


class AdaptiveLearningEndToEndTests(unittest.TestCase):
    """The real round trip: feedback submission -> pattern learned ->
    next analysis reflects it - exercised through the real, sole
    surviving path's IntelligenceOrchestrator._cuad_mitigation, not a
    mock of it. Deviation/jurisdiction/precedent tools are mocked (same
    pattern as test_cuad_mitigation_observability.py) since this test is
    about the adaptive-learning wiring specifically, not those tools."""

    def _run_cuad_mitigation(self, graph, clauses, violations, contract_text="Sample contract text."):
        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None
        state = {
            "extracted_clauses": clauses, "policy_violations": violations,
            "contract_text": contract_text, "contract_id": "contract_1", "tenant_id": "tenant_1",
            "risk_data": {}, "node_status": {},
        }

        with patch("backend.agents.optimized_cuad_tools.OptimizedDeviationDetectorTool") as MockDev, \
             patch("backend.agents.optimized_cuad_tools.OptimizedJurisdictionAdapterTool") as MockJur, \
             patch("backend.agents.optimized_cuad_tools.OptimizedPrecedentMatcherTool") as MockPrec, \
             patch("backend.validation.cuad_validator.validate_cuad_analysis") as mock_validate, \
             patch("backend.infrastructure.audit_logger.AuditLogger.log_event"), \
             patch("backend.infrastructure.contract_repository.graph", graph):
            MockDev.return_value._run.return_value = "[]"
            MockJur.return_value._run.return_value = '{"jurisdiction": "unknown", "industry": "general"}'
            MockPrec.return_value._run.return_value = "[]"
            mock_validate.return_value = MagicMock(is_valid=True, confidence_score=0.9)

            result = orchestrator._cuad_mitigation(state)

        return result["extracted_clauses"]

    def test_learned_pattern_changes_next_analysis_risk_level(self):
        graph = FakeDecisionGraph()

        # 1. Feedback submission: 5 real decisions for "Non-Compete" via the
        # real FeedbackCollector.collect_decision - the outer gate in
        # PatternLearner.learn_from_decisions requires >= 5 total decisions
        # for a clause_type before it will learn anything at all.
        for i in range(5):
            _submit_decision(graph, i, "Non-Compete", original_risk="HIGH", override_risk="CRITICAL")

        # 2. Next analysis: a Non-Compete clause with NO matching policy
        # violation, so its baseline comes from the inherent-high-risk
        # clause-type table (HIGH) - matching the original_risk="HIGH" the
        # decisions above were recorded against.
        clauses = [{"clause_id": "target_clause", "clause_type": "Non-Compete",
                    "content": "Employee shall not compete for 2 years.", "confidence_score": 0.9}]

        result_clauses = self._run_cuad_mitigation(graph, clauses, violations=[])

        self.assertEqual(len(result_clauses), 1)
        clause = result_clauses[0]
        # 3. Pattern learned and applied: risk_level actually changed to
        # what the historical decisions overrode it to - not left at the
        # baseline, and not stuck in a sibling field nothing reads.
        self.assertEqual(clause["risk_level"], "CRITICAL")
        # 4. Traceable: the real computed baseline is preserved, not
        # silently lost when the override applied.
        self.assertEqual(clause["original_risk_level"], "HIGH")
        self.assertEqual(clause["learned_risk_adjustment"], "CRITICAL")
        self.assertEqual(clause["pattern_confidence"], 1.0)  # 5/5 decisions agreed
        self.assertIn("risk_override_Non-Compete", clause["risk_adjustment_pattern_id"])

    def test_clause_with_no_applicable_pattern_still_gets_real_nonconstant_baseline(self):
        """No decisions submitted for this clause_type at all - the clause
        must still land on a real, computed baseline (not the old "LOW"
        default), just without any learned override on top."""
        graph = FakeDecisionGraph()  # empty - no decisions for anything

        clauses = [{"clause_id": "target_clause_2", "clause_type": "Uncapped Liability",
                    "content": "Liability shall be unlimited.", "confidence_score": 0.9}]

        result_clauses = self._run_cuad_mitigation(graph, clauses, violations=[])

        self.assertEqual(len(result_clauses), 1)
        clause = result_clauses[0]
        self.assertEqual(clause["risk_level"], "HIGH")  # inherent-high-risk clause type fallback
        self.assertNotEqual(clause["risk_level"], "LOW")
        # No pattern matched - no adjustment fields populated.
        self.assertNotIn("learned_risk_adjustment", clause)
        self.assertNotIn("original_risk_level", clause)


if __name__ == "__main__":
    unittest.main()
