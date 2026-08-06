"""
Real A-F quality grading for a completed analysis, built entirely on
signals PlanExecutionEngine already computes - node_status (P1's honest
partial-failure reporting, now circuit-breaker-aware - see execution_
engine.py's _compute_step_status), processing_complete, and each
extracted clause's own `grounded`/`confidence_score` fields. Nothing here
is a new measurement; it's a deterministic rubric over numbers that
already exist in the real result.

This is a new module, not a resurrection of the deleted quality_scorer.py
(backend/agents/supervisor/ - see docs/CAPSTONE_SUMMARY.md §8): that file
computed a grade that was logged and never gated or surfaced anywhere.
This one is wired into PlanExecutionEngine._format_final_results itself
(so it's part of the one real result every analysis path returns) and its
grade/escalation state is what POST /api/supervisor/workflow/execute
surfaces - a grade with a real consumer, not a decorative log line.
"""

from typing import Any, Dict, List

CORE_STEP_TYPES = ("extract_clauses", "check_policies", "assess_risk")


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


def grade_analysis(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    result is exactly what PlanExecutionEngine._format_final_results (or
    _format_error_results) returns. Deterministic, ordered rules - first
    match wins:

    F: any core step (extract_clauses/check_policies/assess_risk) is
       "failed", or grounded_rate < 0.5
    D: processing_complete is False for another reason, or any step is
       "partial" and grounded_rate < 0.7
    C: any step is "partial" (grounded_rate >= 0.7)
    B: everything "success" but grounded_rate < 0.9 or avg_confidence < 0.85
    A: everything "success", grounded_rate >= 0.9, avg_confidence >= 0.85
    """
    node_status: Dict[str, str] = result.get("node_status") or {}
    processing_complete: bool = bool(result.get("processing_complete", False))
    clauses: List[Dict[str, Any]] = result.get("clauses") or []

    grounded_rate = _grounded_rate(clauses)
    avg_confidence = _avg_confidence(clauses)
    any_partial = any(status == "partial" for status in node_status.values())
    core_failed = any(node_status.get(step) == "failed" for step in CORE_STEP_TYPES)

    if core_failed or grounded_rate < 0.5:
        grade = "F"
        reasons = _reasons(core_failed, grounded_rate, "core step failed" if core_failed else None)
    elif not processing_complete:
        grade = "D"
        reasons = _reasons(False, grounded_rate, "a non-core step did not fully complete")
    elif any_partial and grounded_rate < 0.7:
        grade = "D"
        reasons = _reasons(False, grounded_rate, "partial step combined with a low grounding rate")
    elif any_partial:
        grade = "C"
        reasons = _reasons(False, grounded_rate, "at least one step only partially completed")
    elif grounded_rate < 0.9 or avg_confidence < 0.85:
        grade = "B"
        reasons = _reasons(False, grounded_rate, None, avg_confidence)
    else:
        grade = "A"
        reasons = ["all steps succeeded", f"grounded_rate={grounded_rate:.2f}", f"avg_confidence={avg_confidence:.2f}"]

    return {
        "grade": grade,
        "grounded_rate": round(grounded_rate, 4),
        "avg_confidence": round(avg_confidence, 4),
        "node_status": node_status,
        "reasons": reasons,
    }


def _reasons(core_failed: bool, grounded_rate: float, extra: Any = None, avg_confidence: float = None) -> List[str]:
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
    if extra:
        reasons.append(extra)
    return reasons or ["all core steps succeeded"]
