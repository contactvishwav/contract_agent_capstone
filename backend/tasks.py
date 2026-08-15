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
from backend.application.services.intelligence_result_serializer import (
    intelligence_to_response_dict as _intelligence_to_response_dict,
)
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


@celery_app.task(bind=True, name="analyze_contract")
def analyze_contract_task(self, contract_id: str, tenant_id: str, model: str = "gemini-2.5-flash", use_planning: bool = False) -> Dict[str, Any]:
    """
    Run the real multi-agent analysis pipeline and return the same shape
    the synchronous route used to return directly. Raises on failure
    (contract not found, or any exception from the pipeline) rather than
    swallowing it into a plausible-looking result - Celery marks the task
    FAILURE and the exception is available via AsyncResult.info, matching
    the "don't mask failure" discipline already built into the rest of
    this pipeline (P1's honest partial-failure reporting) rather than
    reintroducing the same problem at the task-queue layer.

    Phase 4 (HITL) exception: AnalysisPendingReviewError (the traditional-
    workflow graph paused at human_review_gate for a HIGH/CRITICAL-risk
    contract) is a legitimate, honest outcome, not a failure - caught here
    and returned as a normal SUCCESS task with a distinguishable
    "status": "PENDING_HUMAN_REVIEW" payload, the same discriminator
    GET .../status and the frontend poller check for. The Contract node's
    own pending_human_review state (ContractIntelligenceService.
    _mark_pending_review) is already persisted by the time this exception
    reaches here, so it survives independently of this task result's TTL.
    """
    from backend.application.services.contract_intelligence_service import (
        AnalysisPendingReviewError, ContractIntelligenceServiceFactory,
    )

    logger.info(f"[task {self.request.id}] Starting intelligence analysis for contract: {contract_id}")

    intelligence_service = ContractIntelligenceServiceFactory.create_service(_llm_manager)
    try:
        intelligence = asyncio.run(
            intelligence_service.analyze_contract_by_id(contract_id, tenant_id, model, use_planning)
        )
    except AnalysisPendingReviewError as e:
        logger.info(f"[task {self.request.id}] Contract {contract_id} paused for human review ({e.risk_level})")
        return {
            "contract_id": contract_id,
            "status": "PENDING_HUMAN_REVIEW",
            "analysis_complete": False,
            "model_used": model,
            "risk_level": e.risk_level,
            "risk_score": e.overall_risk_score,
        }

    if not intelligence:
        raise ValueError(f"Contract {contract_id} not found or has no content")

    logger.info(f"[task {self.request.id}] Intelligence analysis completed for contract: {contract_id}")
    return _intelligence_to_response_dict(contract_id, model, intelligence)
