"""
Celery tasks.

Scoped deliberately to one operation: contract analysis
(ContractIntelligenceService.analyze_contract_by_id, reached via
POST /api/intelligence/contracts/{contract_id}/analyze) - the one
genuinely long-running (empirically 20-25s+ during live end-to-end
testing, longer under any partial-failure/retry), multi-LLM-call
operation in this system (clause extraction, then one policy-evaluation
call per clause). It's also the exact operation this whole engagement has
already spent the most effort making honest and traceable (audit trail,
node_status, partial-failure reporting) - extending that same honesty
into "is this done yet" via real Celery task state is a natural fit for
this one task, not a wholesale move of every endpoint into async tasks.

Deliberately NOT moved here: batch-analyze (loops over multiple analyze
calls - a natural future extension that could enqueue N of this same
task, not requested now), search/pattern/policy-compliance routes
(single-LLM-call-at-most or no LLM at all), uploads (no LLM calls in the
base path already).
"""

import asyncio
from typing import Any, Dict

from backend.celery_app import celery_app
from backend.llm_manager import LLMManager
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

# Module-level singleton, matching this codebase's existing convention for
# expensive-ish shared clients (shared.cache.redis_cache.cache,
# infrastructure.encryption.field_encryptor) - a Celery worker is a
# separate process from the FastAPI app, so it can't reuse
# app.state.llm_manager and needs its own construction, done once per
# worker process rather than once per task.
_llm_manager = LLMManager()


def _intelligence_to_response_dict(contract_id: str, model: str, intelligence) -> Dict[str, Any]:
    """Same response shape the /analyze route returned synchronously
    before this became a Celery task - a caller polling task status for
    the result sees an identical payload either way."""
    return {
        "contract_id": contract_id,
        "analysis_complete": intelligence.processing_complete,
        "node_status": intelligence.node_status,
        "processing_time": intelligence.processing_time,
        "model_used": model,
        "phase_used": "phase3_optimized",
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


@celery_app.task(bind=True, name="analyze_contract")
def analyze_contract_task(self, contract_id: str, tenant_id: str, model: str = "gemini-2.5-flash", use_planning: bool = True) -> Dict[str, Any]:
    """
    Run the real multi-agent analysis pipeline and return the same shape
    the synchronous route used to return directly. Raises on failure
    (contract not found, or any exception from the pipeline) rather than
    swallowing it into a plausible-looking result - Celery marks the task
    FAILURE and the exception is available via AsyncResult.info, matching
    the "don't mask failure" discipline already built into the rest of
    this pipeline (P1's honest partial-failure reporting) rather than
    reintroducing the same problem at the task-queue layer.
    """
    from backend.application.services.contract_intelligence_service import ContractIntelligenceServiceFactory

    logger.info(f"[task {self.request.id}] Starting intelligence analysis for contract: {contract_id}")

    intelligence_service = ContractIntelligenceServiceFactory.create_service(_llm_manager)
    intelligence = asyncio.run(
        intelligence_service.analyze_contract_by_id(contract_id, tenant_id, model, use_planning)
    )

    if not intelligence:
        raise ValueError(f"Contract {contract_id} not found or has no content")

    logger.info(f"[task {self.request.id}] Intelligence analysis completed for contract: {contract_id}")
    return _intelligence_to_response_dict(contract_id, model, intelligence)
