"""Provider-neutral serialization for persisted and task analysis results."""

from typing import Any, Dict


def intelligence_to_response_dict(contract_id: str, model: str, intelligence) -> Dict[str, Any]:
    """Serialize one analysis identically for Celery, persistence, and HTTP."""
    execution_path = getattr(intelligence, "execution_path", None)
    planned_execution = getattr(intelligence, "planned_execution", None)
    return {
        "contract_id": contract_id,
        "analysis_complete": intelligence.processing_complete,
        "node_status": intelligence.node_status,
        "processing_time": intelligence.processing_time,
        "model_used": model,
        "phase_used": execution_path or "unknown",
        "execution_path": execution_path,
        "planned_execution": planned_execution,
        "quality_grade": intelligence.quality_grade,
        "escalated": intelligence.escalated,
        "analysis_method": intelligence.analysis_method,
        "results": {
            "clauses": [
                {
                    "clause_id": clause.clause_id,
                    "clause_type": clause.clause_type,
                    "content": clause.content,
                    "risk_level": clause.risk_level,
                    "confidence_score": clause.confidence_score,
                    "location": clause.location,
                    "grounded": clause.grounded,
                    "original_risk_level": clause.original_risk_level,
                    "learned_risk_adjustment": clause.learned_risk_adjustment,
                    "pattern_confidence": clause.pattern_confidence,
                    "risk_adjustment_pattern_id": clause.risk_adjustment_pattern_id,
                }
                for clause in intelligence.clauses
            ],
            "violations": [
                {
                    "clause_id": violation.clause_id,
                    "clause_type": violation.clause_type,
                    "issue": violation.issue,
                    "severity": violation.severity,
                    "suggested_fix": violation.suggested_fix,
                    "clause_content": violation.clause_content,
                    "clause_grounded": violation.clause_grounded,
                }
                for violation in intelligence.violations
            ],
            "risk_assessment": {
                "overall_risk_score": intelligence.risk_assessment.overall_risk_score,
                "risk_level": intelligence.risk_assessment.risk_level,
                "critical_issues": intelligence.risk_assessment.critical_issues,
                "critical_issue_details": intelligence.risk_assessment.critical_issue_details,
                "recommendations": intelligence.risk_assessment.recommendations,
            },
            "redlines": [
                {
                    "original_text": redline.original_text,
                    "suggested_text": redline.suggested_text,
                    "justification": redline.justification,
                    "priority": redline.priority,
                }
                for redline in intelligence.redlines
            ],
            "cuad_analysis": {
                "deviations": getattr(intelligence, "cuad_deviations", []),
                "jurisdiction": getattr(intelligence, "jurisdiction_info", {}),
                "precedent_matches": getattr(intelligence, "precedent_matches", []),
                "performance_optimized": True,
                "cache_enabled": True,
            },
        },
    }
