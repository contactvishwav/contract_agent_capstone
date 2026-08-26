"""Real A-F quality grading for a completed traditional-path analysis run.

Salvaged from backend/agents/supervisor/quality_grader.py (removed along
with the rest of PlanExecutionEngine - see git history and README.md's
pipeline description for why) - same deterministic rubric philosophy,
adapted to real data this session's own observability work confirmed is
actually available on the traditional LangGraph path:

- node_status (backend/agents/contract_intelligence_agents.py): unlike
  PlanExecutionEngine's 3-value success/partial/failed system, most
  traditional-path nodes only ever report success/error - except
  policy_checking, which genuinely can report "partial" too (some
  clauses failed evaluation, not all - PolicyCheckerTool._run). Both
  real vocabularies are handled here, not just one.
- Each extracted clause's own grounded/confidence_score fields (same
  signals the original rubric used).
- CUAD Mitigation's own validate_cuad_analysis result (validated/
  confidence_score) - real on all 3 fallback tiers as of this session's
  Phase 2/Phase 1 validation wiring, not just the Phase 3/optimized
  tier. A real, non-hardcoded degradation signal distinct from node_
  status: cuad_mitigation can report "success" (it ran and produced a
  result) while its own validator still flags that result as not fully
  trustworthy (is_valid=False) - a case the node-status-only view alone
  can't see.

Nothing here is a new measurement - it's a deterministic rubric over
numbers the traditional path already computes for real.
"""

from typing import Any, Dict, List, Optional

# The traditional path's own node_status key names (contract_intelligence_
# agents.py) - clause_extraction/policy_checking/risk_calculation are the
# same "extraction, compliance, risk" trio the original rubric treated as
# core; cuad_mitigation/redline_generation are downstream of an already-
# complete risk assessment, same as the original excluded cuad_mitigation/
# generate_redlines from its own core set.
CORE_NODES = ("clause_extraction", "policy_checking", "risk_calculation")


def _grounded_rate(clauses: List[Dict[str, Any]]) -> float:
    if not clauses:
        return 1.0  # nothing to be ungrounded - not itself a grading signal
    grounded = sum(1 for c in clauses if c.get("grounded", True))
    return grounded / len(clauses)


def _avg_confidence(clauses: List[Dict[str, Any]]) -> float:
    scores = [c.get("confidence_score") for c in clauses if isinstance(c.get("confidence_score"), (int, float))]
    if not scores:
        return 1.0  # nothing to average - not itself a grading signal
    return sum(scores) / len(scores)


def grade_run(
    node_status: Dict[str, str],
    processing_complete: bool,
    clauses: List[Dict[str, Any]],
    validation_result: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    validation_result is CUAD Mitigation's real validate_cuad_analysis
    output (backend/validation/cuad_validator.py's ValidationResult) when
    available - None only when cuad_mitigation failed on every tier (no
    tier ever ran the validator) or genuinely wasn't reached.

    Deterministic, ordered rules - first match wins:

    F: a core node (clause_extraction/policy_checking/risk_calculation)
       is "error", or grounded_rate < 0.5
    D: processing_complete is False for another reason, or the run is
       "degraded" (see below) combined with grounded_rate < 0.7
    C: the run is "degraded" (grounded_rate >= 0.7) - any node reports
       "partial", a non-core node ("cuad_mitigation"/"redline_generation")
       reports "error", or CUAD Mitigation's own validator flagged its
       result (validation_result.is_valid is False)
    B: everything otherwise clean but grounded_rate < 0.9, avg_confidence
       < 0.85, or validation_result.confidence_score < 0.85
    A: everything "success", grounded_rate >= 0.9, avg_confidence >= 0.85,
       and (no validation signal, or validation_result.confidence_score
       >= 0.85)
    """
    node_status = node_status or {}
    clauses = clauses or []

    grounded_rate = _grounded_rate(clauses)
    avg_confidence = _avg_confidence(clauses)

    core_failed = any(node_status.get(step) == "error" for step in CORE_NODES)
    any_partial = any(status == "partial" for status in node_status.values())
    non_core_failed = any(
        status == "error" for node, status in node_status.items() if node not in CORE_NODES
    )
    validation_flagged = validation_result is not None and not getattr(validation_result, "is_valid", True)
    validation_confidence = getattr(validation_result, "confidence_score", None) if validation_result is not None else None
    degraded = any_partial or non_core_failed or validation_flagged

    if core_failed or grounded_rate < 0.5:
        grade = "F"
        reasons = _reasons(core_failed, grounded_rate, "a core step (extraction, policy check, or risk assessment) failed" if core_failed else None)
    elif not processing_complete:
        grade = "D"
        reasons = _reasons(False, grounded_rate, "the run did not fully complete")
    elif degraded and grounded_rate < 0.7:
        grade = "D"
        reasons = _reasons(False, grounded_rate, _degraded_reason(any_partial, non_core_failed, validation_flagged))
    elif degraded:
        grade = "C"
        reasons = _reasons(False, grounded_rate, _degraded_reason(any_partial, non_core_failed, validation_flagged))
    elif grounded_rate < 0.9 or avg_confidence < 0.85 or (validation_confidence is not None and validation_confidence < 0.85):
        grade = "B"
        reasons = _reasons(False, grounded_rate, None, avg_confidence, validation_confidence)
    else:
        grade = "A"
        reasons = ["all steps succeeded", f"grounded_rate={grounded_rate:.2f}", f"avg_confidence={avg_confidence:.2f}"]
        if validation_confidence is not None:
            reasons.append(f"cuad_validation_confidence={validation_confidence:.2f}")

    return {
        "grade": grade,
        "grounded_rate": round(grounded_rate, 4),
        "avg_confidence": round(avg_confidence, 4),
        "validation_confidence": round(validation_confidence, 4) if validation_confidence is not None else None,
        "node_status": node_status,
        "reasons": reasons,
    }


def _degraded_reason(any_partial: bool, non_core_failed: bool, validation_flagged: bool) -> str:
    if any_partial:
        return "at least one step only partially completed"
    if non_core_failed:
        return "a non-core step (CUAD mitigation or redline generation) failed"
    return "CUAD Mitigation's own validator flagged this result (is_valid=False)"


def _reasons(
    core_failed: bool,
    grounded_rate: float,
    extra: Any = None,
    avg_confidence: float = None,
    validation_confidence: float = None,
) -> List[str]:
    reasons = []
    if core_failed:
        reasons.append("a core step (extraction, policy check, or risk assessment) failed")
    if grounded_rate < 0.5:
        reasons.append(f"grounded_rate={grounded_rate:.2f} is below 0.5 - most extracted clauses could not be verified against the source text")
    elif grounded_rate < 0.7:
        reasons.append(f"grounded_rate={grounded_rate:.2f} is below 0.7")
    elif grounded_rate < 0.9:
        reasons.append(f"grounded_rate={grounded_rate:.2f} is below 0.9")
    if avg_confidence is not None and avg_confidence < 0.85:
        reasons.append(f"avg_confidence={avg_confidence:.2f} is below 0.85")
    if validation_confidence is not None and validation_confidence < 0.85:
        reasons.append(f"cuad_validation_confidence={validation_confidence:.2f} is below 0.85")
    if extra:
        reasons.append(extra)
    return reasons or ["all core steps succeeded"]
