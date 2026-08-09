from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Depends, Request
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity
from backend.application.services.enhanced_document_processing_service import EnhancedDocumentServiceFactory
from backend.domain.entities import DocumentProcessingRequest
from backend.infrastructure.content_validator import ContentValidationService
from backend.llm_manager import LLMManager
from backend.model_registry import ModelSelectionError, validate_model
import os
import uuid
import hashlib
import logging
from typing import Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/documents/enhanced", tags=["enhanced-documents"])

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager

@router.post("/upload")
async def upload_pdf_enhanced(
    file: UploadFile = File(...),
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for processing"),
    enable_embeddings: bool = Query(default=True, description="Enable multi-level embeddings processing"),
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.UPLOAD)),
):
    """
    Upload and process PDF contract with enhanced multi-level embeddings
    - Validates file type and size
    - Processes using enhanced PDF processing agent
    - Generates document, section, clause, and relationship embeddings
    - Returns processing status with embedding details
    """
    tenant_id = identity.tenant_id
    try:
        validate_model(model, "upload")
    except ModelSelectionError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "category": exc.category})
    if model not in llm_mgr.raw_llms:
        raise HTTPException(status_code=503, detail="Selected model is temporarily unavailable")

    logger.info(f"=== ENHANCED UPLOAD START: {file.filename if file else 'NO FILE'} ===")
    
    try:
        # Input validation
        logger.info(f"Step 1: Input validation for file: {file.filename}")
        if not file.filename:
            logger.error("No filename provided")
            raise HTTPException(status_code=400, detail="No filename provided")
        
        if not file.filename.lower().endswith('.pdf'):
            logger.error(f"Invalid file type: {file.filename}")
            raise HTTPException(status_code=400, detail="Only PDF files are supported")

        # Check file size (50MB limit)
        logger.info("Step 2: Reading file content")
        file_content = await file.read()
        source_hash = hashlib.sha256(file_content).hexdigest()
        logger.info(f"File size: {len(file_content)} bytes")
        if len(file_content) > 50 * 1024 * 1024:
            logger.error(f"File too large: {len(file_content)} bytes")
            raise HTTPException(status_code=400, detail="File too large (max 50MB)")

        # Duplicate identity is exact content within the authenticated tenant.
        # A filename is neither unique nor a security boundary.
        logger.info("Step 3: Checking for duplicates")
        try:
            from backend.infrastructure.contract_repository import Neo4jContractRepository
            repo = Neo4jContractRepository()
            
            existing_query = """
            MATCH (c:Contract {tenant_id: $tenant_id, source_hash: $source_hash})
            WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
            RETURN c.file_id AS file_id LIMIT 1
            """
            existing = repo.graph.query(
                existing_query,
                {"tenant_id": tenant_id, "source_hash": source_hash},
            )
            logger.info(f"Duplicate check completed. Found: {len(existing) if existing else 0} matches")
        except Exception as query_error:
            logger.error(f"Duplicate check query failed: {query_error}")
            existing = []
        
        if existing:
            contract_id = existing[0]["file_id"]
            from backend.application.services.pdf_provenance_service import PdfProvenanceService
            provenance = PdfProvenanceService()
            if not provenance.source_record(contract_id, tenant_id):
                from backend.infrastructure.text_extractors import (
                    TextExtractionService,
                    extract_pages_from_bytes_async,
                )
                extraction = await extract_pages_from_bytes_async(
                    TextExtractionService(), file_content
                )
                provenance.persist_source(
                    contract_id=contract_id,
                    tenant_id=tenant_id,
                    filename=file.filename,
                    pdf_bytes=file_content,
                    extraction=extraction,
                    source_hash=source_hash,
                )
            return {
                "message": "Duplicate file detected",
                "filename": file.filename,
                "status": "duplicate",
                "contract_id": contract_id,
                "existing_contract_id": contract_id,
                "action": "skipped",
                "details": "Identical tenant-owned contract already exists; source provenance is available.",
                "model_used": model,
                "enhanced_embeddings": False,
            }

        # Save file temporarily
        logger.info("Step 4: Saving file temporarily")
        temp_filename = f"{uuid.uuid4().hex}_{file.filename}"
        temp_path = f"/tmp/{temp_filename}"
        
        try:
            with open(temp_path, "wb") as temp_file:
                temp_file.write(file_content)
            logger.info(f"File saved successfully: {file.filename} -> {temp_path}")
        except Exception as save_error:
            logger.error(f"Failed to save file: {save_error}")
            raise

        # Extract full text for storage
        logger.info("Step 5: Extracting text from PDF")
        try:
            from backend.infrastructure.text_extractors import TextExtractionService, extract_pages_async
            text_extractor = TextExtractionService()
            # extract_text_async, not extract_with_fallback directly - see
            # text_extractors.py's docstring: a real production incident
            # traced a client-visible 504 (and the backend going
            # unresponsive to its own health check) to this exact
            # synchronous, CPU-bound call blocking the single-worker
            # event loop for the /api/documents/upload route; this route
            # has the identical shape and the identical risk.
            page_extraction = await extract_pages_async(text_extractor, temp_path)
            full_text = page_extraction.full_text
            logger.info(f"Text extraction completed. Length: {len(full_text)} characters")

            # Content/PII validation (P3 item 21) - this endpoint previously
            # had none at all, unlike /api/documents/upload. Non-blocking:
            # actual PII redaction happens at the storage layer regardless
            # (see Neo4jContractRepository.store_contract); this surfaces a
            # warning for visibility/audit rather than rejecting the upload.
            content_validation = ContentValidationService().validate({"full_text": full_text})
            if content_validation["has_warnings"]:
                logger.warning(f"Content validation warnings for {file.filename}: {content_validation['summary']}")
        except Exception as extract_error:
            logger.error(f"Text extraction failed: {extract_error}")
            raise

        # Create enhanced processing request
        logger.info("Step 6: Creating enhanced processing request")
        try:
            processing_request = DocumentProcessingRequest(
                file_path=temp_path,
                filename=file.filename,
                tenant_id=tenant_id,
                processing_options={
                    "model": model, 
                    "full_text": full_text,
                    "enable_embeddings": enable_embeddings
                }
            )
            logger.info("Enhanced processing request created successfully")
        except Exception as request_error:
            logger.error(f"Failed to create processing request: {request_error}")
            raise

        # Process with enhanced embeddings
        logger.info(f"Step 7: Starting enhanced document processing for: {file.filename}")
        try:
            if enable_embeddings:
                # Create service with injected agent manager
                enhanced_document_service = EnhancedDocumentServiceFactory.create_service(llm_mgr)
                result = await enhanced_document_service.process_pdf_with_embeddings(processing_request)
            else:
                # Fallback to regular processing
                from backend.application.services.document_processing_service import DocumentServiceFactory
                regular_service = DocumentServiceFactory.create_service(llm_mgr)
                result = await regular_service.process_pdf_upload(processing_request)
                result["enhanced_embeddings"] = False
            
            logger.info(f"Enhanced document processing completed successfully: {result}")
        except Exception as proc_error:
            logger.error(f"Enhanced document processing failed: {str(proc_error)}")
            import traceback
            logger.error(f"Processing traceback: {traceback.format_exc()}")
            
            return {
                "message": "Enhanced PDF processing failed",
                "filename": file.filename,
                "status": "error",
                "contract_id": None,
                "details": f"Processing error: {str(proc_error)}",
                "model_used": model,
                "enhanced_embeddings": False,
                "error_type": type(proc_error).__name__
            }
        
        logger.info(f"Enhanced PDF processing completed for {file.filename}: {result['status']}")
        
        # Extract contract ID from result
        contract_id = result.get("contract_id")
        if not contract_id and "SUCCESS: Contract stored with ID:" in result.get("final_result", ""):
            contract_id = result["final_result"].split("SUCCESS: Contract stored with ID:")[-1].strip()

        if contract_id and result.get("status") != "error":
            finalized = repo.graph.query(
                "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
                "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
                "SET c.source_hash = $source_hash RETURN c.file_id AS contract_id",
                {
                    "contract_id": contract_id,
                    "tenant_id": tenant_id,
                    "source_hash": source_hash,
                },
            )
            if not finalized:
                raise RuntimeError("Uploaded contract metadata could not be finalized")
            from backend.application.services.pdf_provenance_service import PdfProvenanceService
            PdfProvenanceService().persist_source(
                contract_id=contract_id,
                tenant_id=tenant_id,
                filename=file.filename,
                pdf_bytes=file_content,
                extraction=page_extraction,
                source_hash=source_hash,
            )
        
        return {
            "message": "Enhanced PDF processing completed",
            "filename": file.filename,
            "status": result["status"],
            "contract_id": contract_id,
            "details": result.get("final_result", ""),
            "model_used": model,
            "enhanced_embeddings": result.get("enhanced_embeddings", enable_embeddings),
            "embedding_types": ["document", "section", "clause", "relationship"] if result.get("enhanced_embeddings") else []
        }
        
    except HTTPException:
        logger.error(f"HTTP Exception in enhanced upload: {file.filename if file else 'unknown'}")
        raise
    except Exception as e:
        logger.error(f"=== ENHANCED UPLOAD FAILED: {file.filename if file else 'unknown'} ===")
        logger.error(f"Error: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Enhanced processing failed: {str(e)}")
    finally:
        # Cleanup temp file unconditionally (P3 item 21) - previously this
        # only ran in the except branch above, so the raw uploaded PDF
        # (containing full unredacted contract text) was left on disk
        # indefinitely on every successful upload.
        if 'temp_path' in locals() and temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                logger.info(f"Cleaned up temp file: {temp_path}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup temp file: {cleanup_error}")
        logger.info(f"=== ENHANCED UPLOAD END: {file.filename if file else 'unknown'} ===")

@router.get("/embedding-status/{contract_id}")
async def get_embedding_status(
    contract_id: str,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Get embedding status for a specific contract"""
    try:
        from backend.shared.utils.contract_search_tool import graph

        # Check embedding status
        query = """
        MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
        WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
        OPTIONAL MATCH (c)-[:HAS_SECTION]->(s:Section {tenant_id: $tenant_id})
        OPTIONAL MATCH (c)-[:CONTAINS_CLAUSE]->(cl:Clause {tenant_id: $tenant_id})
        OPTIONAL MATCH (c)<-[r:PARTY_TO]-()
        RETURN 
            c.document_embedding IS NOT NULL as has_document_embedding,
            c.summary_embedding IS NOT NULL as has_summary_embedding,
            count(DISTINCT s) as section_count,
            count(DISTINCT cl) as clause_count,
            count(DISTINCT r) as relationship_count,
            count(DISTINCT CASE WHEN r.embedding IS NOT NULL THEN r END) as relationship_embeddings
        """
        
        result = graph.query(
            query,
            {"contract_id": contract_id, "tenant_id": identity.tenant_id},
        )
        
        if not result:
            raise HTTPException(status_code=404, detail="Contract not found")
        
        data = result[0]
        
        return {
            "contract_id": contract_id,
            "embedding_status": {
                "document_embedding": data["has_document_embedding"],
                "summary_embedding": data["has_summary_embedding"],
                "sections": {
                    "count": data["section_count"],
                    "has_embeddings": data["section_count"] > 0
                },
                "clauses": {
                    "count": data["clause_count"],
                    "has_embeddings": data["clause_count"] > 0
                },
                "relationships": {
                    "count": data["relationship_count"],
                    "embedded_count": data["relationship_embeddings"],
                    "has_embeddings": data["relationship_embeddings"] > 0
                }
            },
            "total_embeddings": (
                (1 if data["has_document_embedding"] else 0) +
                (1 if data["has_summary_embedding"] else 0) +
                data["section_count"] +
                data["clause_count"] +
                data["relationship_embeddings"]
            )
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get embedding status for {contract_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get embedding status: {str(e)}")

@router.get("/status")
async def get_enhanced_upload_status(llm_mgr: LLMManager = Depends(get_llm_manager)):
    """Get system status for enhanced document uploads"""
    return {
        "status": "operational",
        "supported_formats": ["pdf"],
        "max_file_size": "50MB",
        "available_models": list(llm_mgr.agents.keys()),
        "embedding_features": {
            "document_level": True,
            "section_level": True,
            "clause_level": True,
            "relationship_level": True,
            "cuad_clause_types": 41,
            "validation": True
        }
    }
