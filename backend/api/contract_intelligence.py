from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends, Request
from backend.governance.rbac import Permission, requires_permission
from fastapi.responses import StreamingResponse
from backend.application.services.contract_intelligence_service import ContractIntelligenceServiceFactory
from backend.llm_manager import LLMManager
from backend.infrastructure.contract_repository import Neo4jContractRepository
import json
import logging
from typing import Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/intelligence", tags=["contract-intelligence"])

# Repository (stateless)
repository = Neo4jContractRepository()

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager

@router.post("/contracts/{contract_id}/analyze", dependencies=[Depends(requires_permission(Permission.ANALYZE))])
async def analyze_contract_intelligence(
    contract_id: str,
    tenant_id: str = Query(default="default-tenant", description="Tenant ID for data isolation"),
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for analysis"),
    use_planning: bool = Query(default=True, description="Use autonomous planning agent"),
    llm_mgr: LLMManager = Depends(get_llm_manager)
):
    """
    Perform comprehensive contract intelligence analysis using multi-agent system
    - Extracts and classifies key clauses
    - Checks policy compliance
    - Assesses risks and calculates scores
    - Generates redline recommendations
    """
    
    try:
        logger.info(f"Starting intelligence analysis for contract: {contract_id}")
        
        # Create service with injected agent manager
        intelligence_service = ContractIntelligenceServiceFactory.create_service(llm_mgr)
        
        # Perform multi-agent analysis with optional planning
        intelligence = await intelligence_service.analyze_contract_by_id(contract_id, tenant_id, model, use_planning)
        
        if not intelligence:
            raise HTTPException(status_code=404, detail=f"Contract {contract_id} not found or has no content")
        
        # Convert to response format with performance info
        response = {
            "contract_id": contract_id,
            "analysis_complete": True,
            "processing_time": intelligence.processing_time,
            "model_used": model,
            "phase_used": "phase3_optimized",
            "results": {
                "clauses": [
                    {
                        "clause_type": clause.clause_type,
                        "content": clause.content,
                        "risk_level": clause.risk_level,
                        "confidence_score": clause.confidence_score,
                        "location": clause.location
                    }
                    for clause in intelligence.clauses
                ],
                "violations": [
                    {
                        "clause_type": violation.clause_type,
                        "issue": violation.issue,
                        "severity": violation.severity,
                        "suggested_fix": violation.suggested_fix,
                        "clause_content": violation.clause_content
                    }
                    for violation in intelligence.violations
                ],
                "risk_assessment": {
                    "overall_risk_score": intelligence.risk_assessment.overall_risk_score,
                    "risk_level": intelligence.risk_assessment.risk_level,
                    "critical_issues": intelligence.risk_assessment.critical_issues,
                    "recommendations": intelligence.risk_assessment.recommendations
                },
                "redlines": [
                    {
                        "original_text": redline.original_text,
                        "suggested_text": redline.suggested_text,
                        "justification": redline.justification,
                        "priority": redline.priority
                    }
                    for redline in intelligence.redlines
                ],
                "cuad_analysis": {
                    "deviations": getattr(intelligence, 'cuad_deviations', []),
                    "jurisdiction": getattr(intelligence, 'jurisdiction_info', {}),
                    "precedent_matches": getattr(intelligence, 'precedent_matches', []),
                    "performance_optimized": True,
                    "cache_enabled": True
                }
            }
        }
        
        logger.info(f"Intelligence analysis completed for contract: {contract_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Intelligence analysis failed for contract {contract_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

@router.get("/contracts/{contract_id}/status")
async def get_intelligence_status(
    contract_id: str,
    tenant_id: str = Query(default="default-tenant", description="Tenant ID for data isolation"),
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
               c.intelligence_updated as updated
        """

        result = repository.graph.query(query, {"contract_id": contract_id, "tenant_id": tenant_id})
        
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
            "last_updated": contract_data.get("updated")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get intelligence status for {contract_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")

@router.post("/contracts/batch-analyze", dependencies=[Depends(requires_permission(Permission.ANALYZE))])
async def batch_analyze_contracts(
    background_tasks: BackgroundTasks,
    contract_ids: list[str],
    tenant_id: str = Query(default="default-tenant", description="Tenant ID for data isolation"),
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for analysis"),
    llm_mgr: LLMManager = Depends(get_llm_manager)
):
    """
    Batch analyze multiple contracts for intelligence
    Runs in background for large batches
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
                tenant_id,
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

@router.get("/dashboard/summary", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_intelligence_dashboard(
    tenant_id: str = Query(default="default-tenant", description="Tenant ID for data isolation"),
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

        result = repository.graph.query(query, {"tenant_id": tenant_id})
        
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