from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Query, Depends, Request
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity
from fastapi.responses import StreamingResponse
from backend.application.services.document_processing_service import DocumentServiceFactory
from backend.domain.entities import DocumentProcessingRequest
from backend.llm_manager import LLMManager
import os
import uuid
import json
import logging
from typing import Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Production contracts router
router = APIRouter(prefix="/contracts", tags=["contracts"])

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager

@router.post("/upload")
async def upload_contract(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for processing"),
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.UPLOAD)),
):
    """Upload and process PDF contract - PRODUCTION ENDPOINT"""
    tenant_id = identity.tenant_id

    logger.info(f"=== CONTRACT UPLOAD START: {file.filename if file else 'NO FILE'} ===")
    
    try:
        # Input validation
        if not file.filename or not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are supported")

        # File size check (50MB limit)
        file_content = await file.read()
        if len(file_content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large (max 50MB)")

        # Save file temporarily
        temp_filename = f"{uuid.uuid4().hex}_{file.filename}"
        temp_path = f"/tmp/{temp_filename}"
        
        with open(temp_path, "wb") as temp_file:
            temp_file.write(file_content)

        # Create processing request
        processing_request = DocumentProcessingRequest(
            file_path=temp_path,
            filename=file.filename,
            tenant_id=tenant_id,
            processing_options={"model": model}
        )

        # Process with injected LLM manager
        document_service = DocumentServiceFactory.create_service(llm_mgr)
        result = document_service.process_pdf_upload(processing_request)
        
        return {
            "message": "Contract processing completed",
            "filename": file.filename,
            "status": result["status"],
            "contract_id": result.get("contract_id"),
            "model_used": model
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Contract upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@router.get("/status")
async def get_contracts_status(llm_mgr: LLMManager = Depends(get_llm_manager)):
    """Get contracts system status - PRODUCTION ENDPOINT"""
    return {
        "status": "operational",
        "supported_formats": ["pdf"],
        "max_file_size": "50MB",
        "available_models": list(llm_mgr.agents.keys())
    }