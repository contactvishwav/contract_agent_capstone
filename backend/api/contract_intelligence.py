from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends, Request
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity, get_current_identity
from fastapi.responses import StreamingResponse
from backend.application.services.contract_intelligence_service import ContractIntelligenceServiceFactory
from backend.llm_manager import LLMManager
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.infrastructure.encryption import field_encryptor
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

    contract_rows = repository.graph.query(
        "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
        "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
        "RETURN c.analysis_task_state AS task_state, c.analysis_task_id AS task_id",
        {"contract_id": contract_id, "tenant_id": identity.tenant_id},
    )
    if not contract_rows:
        raise HTTPException(status_code=404, detail="Contract not found")
    current_state = contract_rows[0].get("task_state")
    if current_state in {"PENDING", "STARTED"}:
        raise HTTPException(status_code=409, detail="An analysis is already running for this contract")

    try:
        task = task_ownership_store.enqueue(
            analyze_contract_task,
            identity.tenant_id,
            (contract_id, identity.tenant_id, model, use_planning),
        )
    except TaskOwnershipUnavailable:
        raise HTTPException(status_code=503, detail="Analysis queue authorization is unavailable")
    repository.graph.query(
        "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
        "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
        "SET c.intelligence_status = 'processing', c.analysis_task_id = $task_id, "
        "c.analysis_task_state = 'PENDING', "
        "c.analysis_requested_at = datetime(), c.model_used = $model",
        {
            "contract_id": contract_id,
            "tenant_id": identity.tenant_id,
            "task_id": task.id,
            "model": model,
        },
    )
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
        WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
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


@router.get("/contracts/{contract_id}/analysis")
async def get_latest_contract_analysis(
    contract_id: str,
    identity: TokenIdentity = Depends(get_current_identity),
):
    """Return the latest persisted analysis without invoking a model.

    Older contracts predate AnalysisRun persistence. They return an honest
    aggregate-only legacy summary rather than being presented as a complete
    replay or silently causing another paid analysis.
    """
    rows = repository.graph.query(
        """
        MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
        WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
        OPTIONAL MATCH (c)-[:HAS_ANALYSIS]->(a:AnalysisRun {tenant_id: $tenant_id})
        WITH c, a ORDER BY a.created_at DESC
        WITH c, head(collect(a)) AS latest
        RETURN c.filename AS filename,
               c.intelligence_status AS intelligence_status,
               c.risk_score AS risk_score,
               c.risk_level AS risk_level,
               c.violations_count AS violations_count,
               c.clauses_count AS clauses_count,
               c.redlines_count AS redlines_count,
               c.processing_time AS processing_time,
               c.analysis_execution_path AS execution_path,
               c.analysis_planned_execution AS planned_execution,
               c.analysis_method AS analysis_method,
               c.model_used AS model_used,
               c.intelligence_updated AS intelligence_updated,
               c.analysis_task_id AS task_id,
               c.analysis_task_state AS task_state,
               latest.analysis_id AS analysis_id,
               latest.status AS analysis_status,
               latest.result_payload AS result_payload,
               latest.created_at AS analysis_created_at
        """,
        {"contract_id": contract_id, "tenant_id": identity.tenant_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Contract not found")

    row = rows[0]
    if row.get("result_payload"):
        try:
            payload = json.loads(field_encryptor.decrypt(row["result_payload"]))
        except Exception:
            logger.exception("Persisted analysis payload could not be decoded for %s", contract_id)
            raise HTTPException(status_code=500, detail="Persisted analysis is unavailable")
        return {
            "state": row.get("analysis_status") or row.get("intelligence_status"),
            "source": "persisted_analysis",
            "legacy_summary": False,
            "analysis_id": row.get("analysis_id"),
            "created_at": serialize_neo4j_datetime(row.get("analysis_created_at")),
            "filename": row.get("filename") or contract_id,
            "analysis": payload,
        }

    status = row.get("intelligence_status") or "not_analyzed"
    if status == "processing":
        return {
            "state": "processing",
            "source": "task_state",
            "legacy_summary": False,
            "filename": row.get("filename") or contract_id,
            "task_id": row.get("task_id"),
            "task_state": row.get("task_state") or "PENDING",
            "status_url": (
                f"/api/intelligence/tasks/{row['task_id']}/status"
                if row.get("task_id") else None
            ),
        }

    if status in {"completed", "completed_with_errors"}:
        legacy_payload = {
            "contract_id": contract_id,
            "analysis_complete": status == "completed",
            "node_status": {},
            "processing_time": row.get("processing_time"),
            "model_used": row.get("model_used") or "unknown",
            "phase_used": row.get("execution_path") or "unknown",
            "execution_path": row.get("execution_path"),
            "planned_execution": row.get("planned_execution"),
            "analysis_method": row.get("analysis_method"),
            "results": {
                "clauses": [],
                "violations": [],
                "redlines": [],
                "risk_assessment": {
                    "overall_risk_score": row.get("risk_score") or 0,
                    "risk_level": row.get("risk_level") or "UNKNOWN",
                    "critical_issues": [],
                    "critical_issue_details": [],
                    "recommendations": [],
                },
            },
            "summary_counts": {
                "clauses": row.get("clauses_count") or 0,
                "violations": row.get("violations_count") or 0,
                "redlines": row.get("redlines_count") or 0,
            },
        }
        return {
            "state": status,
            "source": "legacy_contract_summary",
            "legacy_summary": True,
            "created_at": serialize_neo4j_datetime(row.get("intelligence_updated")),
            "filename": row.get("filename") or contract_id,
            "analysis": legacy_payload,
        }

    return {
        "state": "not_analyzed",
        "source": "contract",
        "legacy_summary": False,
        "filename": row.get("filename") or contract_id,
        "analysis": None,
    }

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
        WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
          AND c.intelligence_status = 'completed'
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
