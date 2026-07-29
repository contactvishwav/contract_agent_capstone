from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Query, Depends, Request
from backend.governance.rbac import Permission, requires_permission
from fastapi.responses import StreamingResponse
from backend.application.services.document_processing_service import DocumentServiceFactory
from backend.domain.entities import DocumentProcessingRequest
from backend.llm_manager import LLMManager
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType, audit_log
from backend.infrastructure.content_validator import ContentValidationService
from backend.infrastructure.error_tracker import ErrorTracker, ErrorCategory, ErrorSeverity, error_tracking_context
from backend.agents.chunking_agent import ChunkingAgent
from backend.infrastructure.chunking.storage_service import ChunkStorageService
import os
import uuid
import json
import logging
from typing import Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/documents", tags=["documents"])

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager

@router.get("/debug/contracts", dependencies=[Depends(requires_permission(Permission.VIEW_AUDIT))])
async def debug_contracts():
    """Debug endpoint to see all contracts"""
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        repo = Neo4jContractRepository()
        
        query = """
        MATCH (c:Contract)
        RETURN c.file_id as contract_id, 
               c.contract_type as contract_type,
               c.summary as summary,
               c.source as source
        ORDER BY c.upload_date DESC
        """
        
        result = repo.graph.query(query)
        
        contracts = []
        for row in result:
            contracts.append({
                "contract_id": row["contract_id"],
                "contract_type": row["contract_type"],
                "summary": row["summary"][:100] + "..." if row["summary"] and len(row["summary"]) > 100 else row["summary"],
                "source": row["source"]
            })
        
        return {
            "total_contracts": len(contracts),
            "contracts": contracts
        }
        
    except Exception as e:
        logger.error(f"Debug contracts failed: {e}")
        return {"error": str(e)}

@router.get("/debug/contract-types", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def debug_contract_types():
    """Debug endpoint to see contract type distribution"""
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        repo = Neo4jContractRepository()
        
        query = """
        MATCH (c:Contract)
        RETURN c.contract_type as contract_type, count(*) as count
        ORDER BY count DESC
        """
        
        result = repo.graph.query(query)
        
        return {
            "contract_types": [{"type": row["contract_type"], "count": row["count"]} for row in result]
        }
        
    except Exception as e:
        logger.error(f"Debug contract types failed: {e}")
        return {"error": str(e)}

@router.post("/upload", dependencies=[Depends(requires_permission(Permission.UPLOAD))])
@audit_log(AuditEventType.DOCUMENT_UPLOAD, "upload_pdf")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    tenant_id: str = Query(..., description="Tenant ID for data isolation (required)"),
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for processing"),
    enable_enhanced: bool = Query(default=False, description="Enable enhanced processing with sections/clauses"),
    llm_mgr: LLMManager = Depends(get_llm_manager)
):
    """
    Upload and process PDF contract
    - Validates file type and size
    - Processes using PDF processing agent
    - Returns processing status
    """
    
    logger.info(f"=== UPLOAD START: {file.filename if file else 'NO FILE'} ===")
    
    # Initialize services
    audit_logger = AuditLogger()
    validator = ContentValidationService()
    
    with error_tracking_context(
        operation="document_upload",
        category=ErrorCategory.FILE_ERROR,
        severity=ErrorSeverity.HIGH,
        resource_id=file.filename if file else "unknown",
        metadata={"model": model}
    ) as error_context:
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
            logger.info(f"File size: {len(file_content)} bytes")
            
            # Validate file metadata
            validation_data = {
                "filename": file.filename,
                "file_size": len(file_content)
            }
            
            validation_result = validator.validate_file_upload(validation_data)
            
            if not validation_result["is_valid"]:
                audit_logger.log_event(
                    event_type=AuditEventType.VALIDATION_FAILURE,
                    resource_id=file.filename,
                    action="file_validation",
                    status="failure",
                    error_details=json.dumps(validation_result)
                )
                raise HTTPException(
                    status_code=400, 
                    detail=f"Validation failed: {validation_result['summary']}"
                )

            # Check for duplicate by filename
            logger.info("Step 3: Checking for duplicates")
            try:
                duplicate_check = llm_mgr.agents["gemini-2.5-flash"]._llm if hasattr(llm_mgr.agents["gemini-2.5-flash"], '_llm') else llm_mgr.agents["gemini-2.5-flash"]
                from backend.infrastructure.contract_repository import Neo4jContractRepository
                repo = Neo4jContractRepository()
                logger.info("Repository initialized successfully")
            except Exception as repo_error:
                logger.error(f"Repository initialization failed: {repo_error}")
                raise

            # Simple duplicate check by filename
            try:
                existing_query = "MATCH (c:Contract) WHERE c.file_id CONTAINS $filename RETURN c.file_id LIMIT 1"
                existing = repo.graph.query(existing_query, {"filename": file.filename.replace(".pdf", "")})
                logger.info(f"Duplicate check completed. Found: {len(existing) if existing else 0} matches")
            except Exception as query_error:
                logger.error(f"Duplicate check query failed: {query_error}")
                # Continue without duplicate check
                existing = []
            
            if existing:
                return {
                    "message": "Duplicate file detected",
                    "filename": file.filename,
                    "status": "duplicate",
                    "existing_contract_id": existing[0]["file_id"],
                    "action": "skipped"
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
                from backend.infrastructure.text_extractors import TextExtractionService
                text_extractor = TextExtractionService()
                full_text = text_extractor.extract_with_fallback(temp_path)
                logger.info(f"Text extraction completed. Length: {len(full_text)} characters")
                
                # Validate content quality
                content_validation = validator.validate({"full_text": full_text})
                
                if content_validation["has_errors"]:
                    audit_logger.log_event(
                        event_type=AuditEventType.VALIDATION_FAILURE,
                        resource_id=file.filename,
                        action="content_validation",
                        status="failure",
                        error_details=json.dumps(content_validation)
                    )
                    logger.warning(f"Content validation issues: {content_validation['summary']}")
                else:
                    logger.info(f"Content validation passed: {len(full_text)} characters")
                
                # Step 5.5: Enhanced Intelligent Chunking with Embeddings
                logger.info("Step 5.5: Enhanced intelligent chunking with embeddings")
                try:
                    # Initialize embedding service
                    from backend.shared.utils.gemini_embedding_service import GeminiEmbeddingService
                    embedding_service = GeminiEmbeddingService()
                    
                    chunking_agent = ChunkingAgent(embedding_service)
                    contract_id = file.filename.replace('.pdf', '')
                    
                    # Try async enhanced chunking first
                    try:
                        chunking_result = await chunking_agent.process_document(
                            document_id=contract_id,
                            content=full_text,
                            metadata={
                                "filename": file.filename,
                                "document_type": "contract",
                                "file_size": len(full_text)
                            }
                        )
                        
                        if chunking_result["success"]:
                            logger.info(f"Enhanced chunking completed: {chunking_result['chunk_count']} chunks, "
                                      f"strategy: {chunking_result['plan'].strategy_type}, "
                                      f"quality: {chunking_result['quality_assessment']['overall_quality']:.2f}")
                            
                            # Log document analysis insights
                            doc_analysis = chunking_result.get('document_analysis', {})
                            if doc_analysis.get('is_legal_document'):
                                logger.info(f"Legal document detected - sections: {doc_analysis.get('section_count', 0)}, "
                                          f"clauses: {doc_analysis.get('clause_count', 0)}")
                        else:
                            logger.warning("Enhanced chunking failed, falling back to sync method")
                            raise Exception("Enhanced chunking failed")
                            
                    except Exception as async_error:
                        logger.warning(f"Async chunking failed: {async_error}, trying sync method")
                        
                        # Fallback to synchronous chunking
                        chunking_result = chunking_agent.process_document_sync(
                            content=full_text,
                            metadata={
                                "filename": file.filename,
                                "document_type": "contract"
                            }
                        )
                        
                        # Store chunks using existing schema for backward compatibility
                        storage_service = ChunkStorageService()
                        chunk_ids = storage_service.store_chunks(
                            contract_id=contract_id,
                            chunks=chunking_result["chunks"]
                        )
                        
                        logger.info(f"Sync chunking completed: {len(chunk_ids)} chunks, "
                                  f"strategy: {chunking_result['strategy_used']}, "
                                  f"quality: {chunking_result['quality_score']:.2f}")
                    
                except Exception as chunking_error:
                    logger.warning(f"All chunking methods failed, continuing without chunking: {chunking_error}")
                    # System continues normally without chunking - no breaking changes
                
            except Exception as extract_error:
                logger.error(f"Text extraction failed: {extract_error}")
                raise

            # Create processing request
            logger.info("Step 6: Creating processing request")
            try:
                processing_request = DocumentProcessingRequest(
                    file_path=temp_path,
                    filename=file.filename,
                    tenant_id=tenant_id,
                    processing_options={
                        "model": model, 
                        "full_text": full_text,
                        "enable_enhanced": enable_enhanced
                    }
                )
                logger.info("Processing request created successfully")
            except Exception as request_error:
                logger.error(f"Failed to create processing request: {request_error}")
                raise

            # Process synchronously with error handling
            logger.info(f"Step 7: Starting document processing for: {file.filename}")
            try:
                # Check if enhanced processing is requested
                enable_enhanced = processing_request.processing_options.get("enable_enhanced", False)
                
                if enable_enhanced:
                    # Use enhanced processor with sections/clauses
                    from backend.factories.document_processor_factory import DocumentProcessorFactory
                    processor = DocumentProcessorFactory.create_processor("full", llm_mgr.agents[model])
                    # Ensure tenant_id is passed in options
                    processing_request.processing_options["tenant_id"] = tenant_id
                    result = await processor.process_document(temp_path, processing_request.processing_options)
                    
                    # Convert to expected format
                    if result["status"] == "success":
                        result = {
                            "status": "success",
                            "contract_id": result["contract_id"],
                            "final_result": f"SUCCESS: Enhanced processing completed. Sections: {result['sections_extracted']}, Clauses: {result['clauses_extracted']}, CUAD: {result['cuad_classifications']}"
                        }
                else:
                    # Use existing basic processing
                    document_service = DocumentServiceFactory.create_service(llm_mgr)
                    result = await document_service.process_pdf_upload(processing_request)
                
                logger.info(f"Document processing completed successfully: {result}")
            except Exception as proc_error:
                logger.error(f"Document processing failed: {str(proc_error)}")
                logger.error(f"Processing error type: {type(proc_error).__name__}")
                import traceback
                logger.error(f"Processing traceback: {traceback.format_exc()}")
                
                # Track processing error
                audit_logger.log_event(
                    event_type=AuditEventType.PROCESSING_ERROR,
                    resource_id=file.filename,
                    action="document_processing",
                    status="failure",
                    error_details=str(proc_error)
                )
                
                # Return error as JSON instead of raising exception
                return {
                    "message": "PDF processing failed",
                    "filename": file.filename,
                    "status": "error",
                    "contract_id": None,
                    "details": f"Processing error: {str(proc_error)}",
                    "model_used": model,
                    "error_type": type(proc_error).__name__
                }
            
            logger.info(f"PDF processing completed for {file.filename}: {result['status']}")
            
            # Extract contract ID from result details if not directly available
            contract_id = result.get("contract_id")
            if not contract_id and "SUCCESS: Contract stored with ID:" in result.get("final_result", ""):
                contract_id = result["final_result"].split("SUCCESS: Contract stored with ID:")[-1].strip()
            
            # Log successful upload
            audit_logger.log_event(
                event_type=AuditEventType.DOCUMENT_UPLOAD,
                resource_id=contract_id or file.filename,
                action="upload_completed",
                status="success",
                metadata={"filename": file.filename, "model": model}
            )
            
            return {
                "message": "PDF processing completed",
                "filename": file.filename,
                "status": result["status"],
                "contract_id": contract_id,
                "details": result.get("final_result", ""),
                "model_used": model,
                "validation_passed": True
            }
        
        except HTTPException:
            logger.error(f"HTTP Exception in upload: {file.filename if file else 'unknown'}")
            raise
        except Exception as e:
            logger.error(f"=== UPLOAD FAILED: {file.filename if file else 'unknown'} ===")
            logger.error(f"Error: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
            # Cleanup temp file if it exists
            if 'temp_path' in locals() and temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                    logger.info(f"Cleaned up temp file: {temp_path}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup temp file: {cleanup_error}")
                    
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
        finally:
            logger.info(f"=== UPLOAD END: {file.filename if file else 'unknown'} ===")

@router.post("/upload-stream", dependencies=[Depends(requires_permission(Permission.UPLOAD))])
async def upload_pdf_stream(
    file: UploadFile = File(...),
    tenant_id: str = Query(..., description="Tenant ID for data isolation (required)"),
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for processing"),
    llm_mgr: LLMManager = Depends(get_llm_manager)
):
    """
    Upload and process PDF with streaming response
    Similar to existing /run/ endpoint pattern
    """
    
    try:
        # Validation (same as above)
        if not file.filename or not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Invalid file")
        
        file_content = await file.read()
        if len(file_content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large")
        
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
        
        # Stream processing results (similar to existing /run/ endpoint)
        async def stream_processing():
            try:
                # Create service with injected LLM manager
                document_service = DocumentServiceFactory.create_service(llm_mgr)
                # Get LLM and create agent
                llm = document_service._get_llm_for_model(model)
                from backend.agents.pdf_processing_agent import PDFAgentFactory
                pdf_agent = PDFAgentFactory.create_agent(llm)
                
                # Create processing message
                from langchain_core.messages import HumanMessage
                processing_message = HumanMessage(content=f"""
                Process this PDF contract document:
                
                File path: {temp_path}
                Filename: {file.filename}
                
                Please:
                1. Extract text from the PDF
                2. Analyze if it's a valid contract
                3. Extract structured contract information
                4. Validate the data quality
                5. Store the contract if validation passes
                """)
                
                messages = [processing_message]
                
                # Create initial state for streaming
                initial_state = {
                    "file_path": temp_path,
                    "tenant_id": tenant_id,
                    "messages": messages,
                    "extracted_text": None,
                    "contract_data": None,
                    "processing_result": None
                }
                
                # Stream results (same pattern as existing system)
                async for chunk in pdf_agent.astream(initial_state, stream_mode=["messages", "updates"]):
                    if chunk[0] == "messages":
                        message = chunk[1]
                        if hasattr(message[0], 'tool_calls') and len(message[0].tool_calls) > 0:
                            for tool in message[0].tool_calls:
                                if tool.get('name'):
                                    tool_calls_content = json.dumps(tool)
                                    yield f"data: {json.dumps({'content': tool_calls_content, 'type': 'tool_call'})}\n\n"
                        
                        if hasattr(message[0], 'content') and message[0].content:
                            yield f"data: {json.dumps({'content': message[0].content, 'type': 'ai_message'})}\n\n"
                
                # Final completion message
                yield f"data: {json.dumps({'content': f'PDF processing completed for {file.filename}', 'type': 'completion'})}\n\n"
                yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"
                
            except Exception as e:
                error_msg = f"Processing failed: {str(e)}"
                yield f"data: {json.dumps({'content': error_msg, 'type': 'error'})}\n\n"
                yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"
            finally:
                # Cleanup
                document_service._cleanup_file(temp_path)
        
        return StreamingResponse(
            stream_processing(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Streaming PDF upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
async def get_upload_status(llm_mgr: LLMManager = Depends(get_llm_manager)):
    """Get system status for document uploads"""
    return {
        "status": "operational",
        "supported_formats": ["pdf"],
        "max_file_size": "50MB",
        "available_models": list(llm_mgr.agents.keys())
    }