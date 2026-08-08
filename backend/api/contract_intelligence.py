from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends, Request
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity, get_current_identity
from fastapi.responses import StreamingResponse
from backend.application.services.contract_intelligence_service import ContractIntelligenceServiceFactory
from backend.llm_manager import LLMManager
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.infrastructure.task_ownership import TaskOwnershipUnavailable, task_ownership_store
import json
import logging
from typing import Optional

from backend.shared.utils.logger import get_logger
from backend.shared.utils.utils import serialize_neo4j_datetime
logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/intelligence", tags=["contract-intelligence"])

# Repository (stateless)
repository = Neo4jContractRepository()

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager

@router.post("/contracts/{contract_id}/analyze", status_code=202)
async def analyze_contract_intelligence(
    contract_id: str,
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for analysis"),
    use_planning: bool = Query(default=True, description="Use autonomous planning agent"),
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """
    Enqueue contract intelligence analysis as a real Celery task - the
    multi-clause extraction + policy evaluation + risk assessment +
    redline generation pipeline is the one genuinely long-running,
    multi-LLM-call operation in this system (backend/tasks.py has the full
    scoping rationale). Returns immediately with a task_id; poll
    GET .../tasks/{task_id}/status for real Celery state (PENDING/STARTED/
    SUCCESS/FAILURE) rather than blocking the request for the full
    duration or getting a fire-and-forget black box.

    tenant_id now comes from the validated token (governance/auth.py), not
    a client-supplied query parameter - a caller can no longer request
    analysis "as" a tenant it doesn't hold a token for.
    """
    from backend.tasks import analyze_contract_task

    try:
        task = task_ownership_store.enqueue(
            analyze_contract_task,
            identity.tenant_id,
            (contract_id, identity.tenant_id, model, use_planning),
        )
    except TaskOwnershipUnavailable:
        raise HTTPException(status_code=503, detail="Analysis queue authorization is unavailable")
    logger.info(f"Enqueued analysis task {task.id} for contract {contract_id} (tenant {identity.tenant_id})")

    return {
        "task_id": task.id,
        "status": "PENDING",
        "contract_id": contract_id,
        "status_url": f"/api/intelligence/tasks/{task.id}/status",
    }

@router.get("/tasks/{task_id}/status")
async def get_analysis_task_status(
    task_id: str,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """
    Poll real Celery task state - not a synthetic/simulated status. A
    Celery SUCCESS only means the task ran to completion without raising;
    the analysis result inside it still carries its own honest
    analysis_complete/node_status (P1) - a "successful" task can still
    report a partial analysis, and that distinction is preserved here, not
    collapsed into one flat status.
    """
    from celery.result import AsyncResult
    from backend.celery_app import celery_app

    try:
        authorized = task_ownership_store.is_owner(task_id, identity.tenant_id)
    except TaskOwnershipUnavailable:
        raise HTTPException(status_code=503, detail="Task status is unavailable")
    if not authorized:
        # Unknown, expired, corrupt, and other-tenant identifiers are
        # deliberately indistinguishable.
        raise HTTPException(status_code=404, detail="Task not found")

    result = AsyncResult(task_id, app=celery_app)

    if result.state == "PENDING":
        return {"task_id": task_id, "status": "PENDING"}
    elif result.state == "STARTED":
        return {"task_id": task_id, "status": "STARTED"}
    elif result.state == "SUCCESS":
        return {"task_id": task_id, "status": "SUCCESS", "result": result.result}
    elif result.state == "FAILURE":
        return {"task_id": task_id, "status": "FAILURE", "error": str(result.info)}
    else:
        return {"task_id": task_id, "status": result.state}

@router.get("/contracts/{contract_id}/status")
async def get_intelligence_status(
    contract_id: str,
    # No specific Permission gate here (there wasn't one before either) -
    # just real authentication, which is now required regardless since
    # tenant_id comes from the token rather than a client-supplied query
    # param.
    identity: TokenIdentity = Depends(get_current_identity),
):
    """Get the current intelligence analysis status for a contract"""

    try:
        # Query contract intelligence status
        query = """
        MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
        RETURN c.intelligence_status as status,
               c.risk_score as risk_score,
               c.risk_level as risk_level,
               c.violations_count as violations_count,
               c.clauses_count as clauses_count,
               c.redlines_count as redlines_count,
               c.processing_time as processing_time,
               c.analysis_execution_path as execution_path,
               c.analysis_planned_execution as planned_execution,
               c.intelligence_updated as updated
        """

        result = repository.graph.query(query, {"contract_id": contract_id, "tenant_id": identity.tenant_id})
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Contract {contract_id} not found")
        
        contract_data = result[0]
        
        return {
            "contract_id": contract_id,
            "intelligence_status": contract_data.get("status", "not_analyzed"),
            "risk_score": contract_data.get("risk_score"),
            "risk_level": contract_data.get("risk_level"),
            "violations_count": contract_data.get("violations_count", 0),
            "clauses_count": contract_data.get("clauses_count", 0),
            "redlines_count": contract_data.get("redlines_count", 0),
            "processing_time": contract_data.get("processing_time"),
            "execution_path": contract_data.get("execution_path"),
            "planned_execution": contract_data.get("planned_execution"),
            # c.intelligence_updated is set via datetime() (contract_
            # intelligence_service.py's _store_intelligence_results), so it
            # comes back as a raw neo4j.time.DateTime object here - same
            # leak class as AuditLogger.get_audit_trail.
            "last_updated": serialize_neo4j_datetime(contract_data.get("updated"))
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get intelligence status for {contract_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")

@router.post("/contracts/batch-analyze")
async def batch_analyze_contracts(
    background_tasks: BackgroundTasks,
    contract_ids: list[str],
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for analysis"),
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """
    Batch analyze multiple contracts for intelligence. Deliberately left
    on FastAPI's own BackgroundTasks rather than moved to Celery along
    with the single-contract /analyze route - out of scope for this pass
    (see backend/tasks.py's docstring); a natural future extension would
    have this enqueue N of the same Celery task instead.
    """

    try:
        logger.info(f"Starting batch analysis for {len(contract_ids)} contracts")

        # For prototype, limit batch size
        if len(contract_ids) > 10:
            raise HTTPException(status_code=400, detail="Batch size limited to 10 contracts for prototype")

        # Create service
        intelligence_service = ContractIntelligenceServiceFactory.create_service(llm_mgr)

        # Add background task for each contract
        for contract_id in contract_ids:
            background_tasks.add_task(
                intelligence_service.analyze_contract_by_id,
                contract_id,
                identity.tenant_id,
                model
            )

        return {
            "message": f"Batch analysis started for {len(contract_ids)} contracts",
            "contract_ids": contract_ids,
            "model": model,
            "status": "processing"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Batch analysis failed: {str(e)}")

@router.get("/dashboard/summary")
async def get_intelligence_dashboard(
    identity: TokenIdentity = Depends(requires_permission(Permission.VIEW_REPORTS)),
):
    """Get summary statistics for intelligence dashboard"""

    try:
        # Query aggregate intelligence statistics
        query = """
        MATCH (c:Contract {tenant_id: $tenant_id})
        WHERE c.intelligence_status = 'completed'
        RETURN
            count(c) as total_analyzed,
            avg(c.risk_score) as avg_risk_score,
            sum(CASE WHEN c.risk_level = 'HIGH' OR c.risk_level = 'CRITICAL' THEN 1 ELSE 0 END) as high_risk_count,
            sum(c.violations_count) as total_violations,
            sum(c.clauses_count) as total_clauses,
            sum(c.redlines_count) as total_redlines
        """

        result = repository.graph.query(query, {"tenant_id": identity.tenant_id})
        
        if result:
            stats = result[0]
            return {
                "total_contracts_analyzed": stats.get("total_analyzed", 0),
                "average_risk_score": round(stats.get("avg_risk_score", 0.0), 2),
                "high_risk_contracts": stats.get("high_risk_count", 0),
                "total_violations_found": stats.get("total_violations", 0),
                "total_clauses_extracted": stats.get("total_clauses", 0),
                "total_redlines_generated": stats.get("total_redlines", 0)
            }
        else:
            return {
                "total_contracts_analyzed": 0,
                "average_risk_score": 0.0,
                "high_risk_contracts": 0,
                "total_violations_found": 0,
                "total_clauses_extracted": 0,
                "total_redlines_generated": 0
            }
        
    except Exception as e:
        logger.error(f"Dashboard summary failed: {e}")
        raise HTTPException(status_code=500, detail=f"Dashboard summary failed: {str(e)}")

@router.get("/models")
async def get_available_models(llm_mgr: LLMManager = Depends(get_llm_manager)):
    """Get list of available LLM models for intelligence analysis"""
    
    try:
        available_models = list(llm_mgr.agents.keys())
        
        return {
            "available_models": available_models,
            "default_model": "gemini-2.5-flash",
            "recommended_models": ["gemini-2.5-flash", "gemini-1.5-pro"]
        }
        
    except Exception as e:
        logger.error(f"Failed to get available models: {e}")
        raise HTTPException(status_code=500, detail=f"Model list failed: {str(e)}")
