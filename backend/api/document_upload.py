from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Query, Form, Request
from fastapi.params import Depends
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity, get_current_identity
from fastapi.responses import Response, StreamingResponse
from backend.application.services.document_processing_service import DocumentServiceFactory
from backend.domain.entities import DocumentProcessingRequest
from backend.llm_manager import LLMManager
from backend.model_registry import ModelSelectionError, validate_model
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType, audit_log
from backend.infrastructure.content_validator import ContentValidationService
from backend.infrastructure.error_tracker import ErrorTracker, ErrorCategory, ErrorSeverity, error_tracking_context
from backend.agents.chunking_agent import ChunkingAgent
from backend.agents.agent_workflow_tracker import workflow_tracker
from backend.infrastructure.chunking.storage_service import ChunkStorageService
from backend.shared.utils.utils import serialize_neo4j_datetime
import os
import uuid
import json
import logging
import hashlib
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/documents", tags=["documents"])

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager


@router.get("")
async def list_uploaded_contracts(
    identity: TokenIdentity = Depends(get_current_identity),
):
    """Return the authenticated tenant's contract-picker metadata.

    Browser localStorage is only an optional cache; it cannot be the source
    of truth for contract ownership or survive a clean browser/session.  The
    tenant predicate is part of the Neo4j MATCH so another tenant's contract
    metadata is never fetched and filtered after the fact.
    """
    from backend.infrastructure.contract_repository import Neo4jContractRepository

    rows = Neo4jContractRepository().graph.query(
        """
        MATCH (c:Contract {tenant_id: $tenant_id})
        WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
        RETURN c.file_id AS contract_id,
               coalesce(c.filename, c.file_id) AS filename,
               c.upload_date AS upload_date,
               coalesce(c.model_used, 'gemini-2.5-flash') AS model_used,
               c.intelligence_status AS intelligence_status,
               c.risk_score AS risk_score,
               c.risk_level AS risk_level
        ORDER BY c.upload_date DESC, c.file_id ASC
        """,
        {"tenant_id": identity.tenant_id},
    )
    return [
        {
            "contract_id": row["contract_id"],
            "filename": row["filename"],
            "upload_date": serialize_neo4j_datetime(row.get("upload_date")),
            "model_used": row["model_used"],
            "analysis_completed": row.get("intelligence_status") in {
                "completed",
                "completed_with_errors",
            },
            "risk_score": row.get("risk_score"),
            "risk_level": row.get("risk_level"),
        }
        for row in rows
    ]

@router.post("/upload")
@audit_log(AuditEventType.DOCUMENT_UPLOAD, "upload_pdf")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    # Real, confirmed bug found live: the frontend (DocumentUpload.tsx)
    # sends `model` as a multipart form field (formData.append('model',
    # modelSelection)), never as a URL query parameter - Query() only
    # binds query-string params, so this always silently resolved to the
    # "gemini-2.5-flash" default regardless of the dropdown's real
    # selection. Form() binds the actual multipart field the UI sends.
    model: str = Form(default="gemini-2.5-flash", description="LLM model to use for processing"),
    enable_enhanced: bool = Query(default=False, description="Enable enhanced processing with sections/clauses"),
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.UPLOAD)),
):
    """
    Upload and process PDF contract
    - Validates file type and size
    - Processes using PDF processing agent
    - Returns processing status
    """
    tenant_id = identity.tenant_id
    try:
        validate_model(model, "upload")
    except ModelSelectionError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "category": exc.category})
    if model not in llm_mgr.raw_llms:
        raise HTTPException(status_code=503, detail="Selected model is temporarily unavailable")

    logger.info(f"=== UPLOAD START: {file.filename if file else 'NO FILE'} ===")
    
    # Initialize services
    audit_logger = AuditLogger()
    validator = ContentValidationService()
    
    with error_tracking_context(
        operation="document_upload",
        category=ErrorCategory.FILE_ERROR,
        severity=ErrorSeverity.HIGH,
        resource_id=file.filename if file else "unknown",
        tenant_id=tenant_id,
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
            source_hash = hashlib.sha256(file_content).hexdigest()
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
                    tenant_id=tenant_id,
                    status="failure",
                    error_details=json.dumps(validation_result)
                )
                raise HTTPException(
                    status_code=400, 
                    detail=f"Validation failed: {validation_result['summary']}"
                )

            # Check for an active byte-identical upload.
            logger.info("Step 3: Checking for duplicates")
            try:
                from backend.infrastructure.contract_repository import Neo4jContractRepository
                repo = Neo4jContractRepository()
                logger.info("Repository initialized successfully")
            except Exception as repo_error:
                logger.error(f"Repository initialization failed: {repo_error}")
                raise

            # Duplicate check by content, scoped to this tenant's active set.
            #
            # Filename is metadata, not identity: corrected documents often
            # retain it. Archived hashes are intentionally ignored so the
            # same bytes can be restored to normal use by uploading again.
            try:
                existing_query = (
                    "MATCH (c:Contract {source_hash: $source_hash, tenant_id: $tenant_id}) "
                    "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
                    "RETURN c.file_id as file_id LIMIT 1"
                )
                existing = repo.graph.query(existing_query, {"source_hash": source_hash, "tenant_id": tenant_id})
                logger.info(f"Duplicate check completed. Found: {len(existing) if existing else 0} matches")
            except Exception as query_error:
                logger.error(f"Duplicate check query failed: {query_error}")
                # Continue without duplicate check
                existing = []
            
            if existing:
                # Real, confirmed bug: this used to omit contract_id/
                # details/model_used - IntelligencePage.tsx only renders
                # the analysis panel when uploadResult.contract_id is
                # truthy, so a duplicate upload dead-ended on the
                # placeholder "Upload a contract to begin analysis" panel
                # while DocumentUpload.tsx's status message fell through to
                # its generic default, "Processing completed: undefined"
                # (details was also missing). Fixed by surfacing the
                # existing contract as `contract_id` too (not just
                # `existing_contract_id`, kept for compatibility) so the
                # user lands on the existing contract's analysis panel
                # instead of a dead end - re-uploading the same file is a
                # very real thing to happen mid-demo, and "here's the
                # contract you already have" is a better outcome than a
                # block.
                existing_contract_id = existing[0]["file_id"]
                # Exact-hash duplicate uploads are the only safe automatic
                # backfill for legacy contracts whose original PDF was deleted
                # by the historical temp-file cleanup. No graph content is
                # reprocessed or replaced: the already-verified bytes are
                # encrypted, page-extracted, and attached idempotently.
                try:
                    from backend.application.services.pdf_provenance_service import PdfProvenanceService
                    from backend.infrastructure.text_extractors import (
                        TextExtractionService,
                        extract_pages_from_bytes_async,
                    )

                    provenance_service = PdfProvenanceService()
                    if not provenance_service.source_record(existing_contract_id, tenant_id):
                        duplicate_extraction = await extract_pages_from_bytes_async(
                            TextExtractionService(), file_content
                        )
                        provenance_service.persist_source(
                            contract_id=existing_contract_id,
                            tenant_id=tenant_id,
                            filename=file.filename,
                            pdf_bytes=file_content,
                            extraction=duplicate_extraction,
                            source_hash=source_hash,
                        )
                except Exception as provenance_error:
                    logger.error(
                        "Source PDF duplicate backfill failed (%s)",
                        type(provenance_error).__name__,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="The existing contract was found, but its source PDF could not be retained",
                    )
                return {
                    "message": f'"{file.filename}" was already uploaded',
                    "filename": file.filename,
                    "status": "duplicate",
                    "contract_id": existing_contract_id,
                    "existing_contract_id": existing_contract_id,
                    "action": "skipped",
                    "details": f"This document already exists (contract ID: {existing_contract_id}). Showing the existing contract instead of re-processing.",
                    "model_used": model
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
                # extract_text_async, not extract_with_fallback directly -
                # a real production incident (504 on upload, backend
                # unresponsive to its own health check for the duration)
                # traced back to this synchronous, CPU-bound call blocking
                # this process's single event loop. See text_extractors.py's
                # docstring for the full incident/fix rationale.
                page_extraction = await extract_pages_async(text_extractor, temp_path)
                full_text = page_extraction.full_text
                logger.info(f"Text extraction completed. Length: {len(full_text)} characters")
                
                # Validate content quality
                content_validation = validator.validate({"full_text": full_text})
                
                if content_validation["has_errors"]:
                    audit_logger.log_event(
                        event_type=AuditEventType.VALIDATION_FAILURE,
                        resource_id=file.filename,
                        action="content_validation",
                        tenant_id=tenant_id,
                        status="failure",
                        error_details=json.dumps(content_validation)
                    )
                    logger.warning(f"Content validation issues: {content_validation['summary']}")
                elif content_validation["has_warnings"]:
                    # Now that SecurityValidator's PII hits actually surface
                    # as warnings (P3 item 21), this is where a PII-in-
                    # content warning becomes visible - non-blocking, since
                    # actual redaction happens at the storage layer
                    # regardless (Neo4jContractRepository.store_contract).
                    logger.warning(f"Content validation warnings: {content_validation['summary']}")
                else:
                    logger.info(f"Content validation passed: {len(full_text)} characters")
                
                # Step 5.5: Enhanced Intelligent Chunking with Embeddings
                logger.info("Step 5.5: Enhanced intelligent chunking with embeddings")
                try:
                    # Initialize embedding service
                    from backend.shared.utils.gemini_embedding_service import GeminiEmbeddingService
                    embedding_service = GeminiEmbeddingService()
                    
                    chunking_agent = ChunkingAgent(embedding_service)
                    from backend.infrastructure.contract_repository import generate_contract_id
                    # Real, confirmed bug found live: this used to be
                    # file.filename.replace('.pdf', '') - not tenant-scoped,
                    # not unique per upload. Two different tenants (or the
                    # same tenant re-uploading) with the same filename wrote
                    # onto one shared Document node forever (storage_service.
                    # py's Chunk writes use CREATE, so nothing even
                    # overwrote cleanly - chunks just piled up under it,
                    # tenant_id last-write-wins). Confirmed live: 14
                    # accumulated Chunk nodes on one Document, from unrelated
                    # uploads across sessions. Fixed by generating the real,
                    # unique, UUID-based id upfront - the same id
                    # store_contract will use for the real Contract node
                    # (threaded through via DocumentProcessingRequest.
                    # contract_id below), not a second, disposable,
                    # collision-prone placeholder.
                    contract_id = generate_contract_id()
                    # `contract_id` is still reassigned below (Step 7) from
                    # the real processing result - normally an identical
                    # value now that both sides agree upfront, but the
                    # enable_enhanced=True branch uses a separate processor
                    # that doesn't thread this id through yet (a separately
                    # flagged, out-of-scope gap - see report) and could
                    # still return a different one. Captured under its own
                    # name so the real->Document link performed after Step 7
                    # always targets the Document chunking actually wrote to,
                    # not whatever contract_id ends up being.
                    chunking_document_id = contract_id
                    async_chunking_succeeded = False

                    # Real bug found live: ChunkingAgent genuinely runs on
                    # every upload (real Chunk nodes, real quality_score,
                    # confirmed via direct Neo4j query) but was never
                    # registered with workflow_tracker at all - invisible to
                    # every observability mechanism, unlike PDF Processing
                    # Agent/Enhanced Embedding Agent right next to it. This
                    # is purely the missing tracking call; the chunking
                    # logic below is unchanged.
                    #
                    # Real, confirmed sequencing bug found live while
                    # verifying this fix: recording the entry immediately
                    # here (start_agent now, complete_agent a few lines
                    # down) doesn't survive - Step 7 below calls
                    # DocumentProcessingService.process_pdf_upload, whose
                    # own _process_with_agent unconditionally calls
                    # workflow_tracker.start_workflow() (resets
                    # self.executions = []) for the real, already-existing
                    # PDF Processing Agent tracking. That reset silently
                    # wiped this entry before GET /api/workflow/status ever
                    # saw it, confirmed live: the real backend log showed
                    # "AGENT COMPLETED: Chunking Agent" firing correctly,
                    # then a later "MULTI-AGENT WORKFLOW COMPLETED" summary
                    # listing only PDF Processing Agent. Fixed by capturing
                    # the real outcome/timing now but deferring the actual
                    # workflow_tracker call until after Step 7's reset has
                    # already happened (see below) - not moving or changing
                    # when chunking itself runs, only when its already-real
                    # result gets reported.
                    chunking_track_start = datetime.now()
                    chunking_track_status = None  # "success" | "error"
                    chunking_track_summary = ""

                    # Try async enhanced chunking first
                    try:
                        chunking_result = await chunking_agent.process_document(
                            document_id=contract_id,
                            content=full_text,
                            metadata={
                                "filename": file.filename,
                                "document_type": "contract",
                                "file_size": len(full_text)
                            },
                            tenant_id=tenant_id
                        )

                        if chunking_result["success"]:
                            # success is determined here, before any
                            # logging - two real, confirmed bugs were found
                            # live in the block below (chunking_result['plan']
                            # accessed as an object's .strategy_type
                            # attribute when it's actually a plain dict on
                            # this path; then, once that was fixed,
                            # chunking_result['quality_assessment']
                            # ['overall_quality'] - a key that was never
                            # actually present in quality_validator.py's
                            # real return shape, which nests per-chunk
                            # scores under 'chunk_scores' instead). Each was
                            # caught by this block's own except below and
                            # misreported as "Async chunking failed",
                            # discarding already-correctly-stored, correctly
                            # tenant-scoped chunks and triggering a wasted,
                            # duplicate sync-fallback write every single
                            # time. async_chunking_succeeded is set
                            # immediately, and the informational logging
                            # below is now wrapped in its own try/except so
                            # a THIRD such mismatch (there is no reason to
                            # assume there isn't one) degrades to a log
                            # warning instead of masking a real success as
                            # a failure again.
                            async_chunking_succeeded = True
                            try:
                                chunk_scores = chunking_result.get('quality_assessment', {}).get('chunk_scores', [])
                                avg_quality = (
                                    sum(s['scores']['overall'] for s in chunk_scores) / len(chunk_scores)
                                    if chunk_scores else 0.0
                                )
                                logger.info(f"Enhanced chunking completed: {chunking_result['chunk_count']} chunks, "
                                          f"strategy: {chunking_result['plan']['strategy_type']}, "
                                          f"quality: {avg_quality:.2f}")

                                doc_analysis = chunking_result.get('document_analysis', {})
                                if doc_analysis.get('is_legal_document'):
                                    logger.info(f"Legal document detected - sections: {doc_analysis.get('section_count', 0)}, "
                                              f"clauses: {doc_analysis.get('clause_count', 0)}")
                            except Exception as log_error:
                                logger.warning(f"Enhanced chunking succeeded but summary logging failed: {log_error}")
                            # Placed outside the summary-logging try above so
                            # a real outcome is always captured even if that
                            # informational logging itself has an issue.
                            chunking_track_status = "success"
                            chunking_track_summary = (
                                f"{chunking_result.get('chunk_count', 0)} chunks, "
                                f"strategy: {chunking_result.get('plan', {}).get('strategy_type', 'unknown')}"
                            )
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
                            chunks=chunking_result["chunks"],
                            tenant_id=tenant_id,
                        )
                        
                        logger.info(f"Sync chunking completed: {len(chunk_ids)} chunks, "
                                  f"strategy: {chunking_result['strategy_used']}, "
                                  f"quality: {chunking_result['quality_score']:.2f}")
                        chunking_track_status = "success"
                        chunking_track_summary = (
                            f"{len(chunk_ids)} chunks (sync fallback), strategy: {chunking_result['strategy_used']}, "
                            f"quality: {chunking_result['quality_score']:.2f}"
                        )

                except Exception as chunking_error:
                    logger.warning(f"All chunking methods failed, continuing without chunking: {chunking_error}")
                    chunking_track_status = "error"
                    chunking_track_summary = str(chunking_error)
                    # System continues normally without chunking - no breaking changes

                chunking_track_end = datetime.now()

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
                    # Same real, UUID-based id Step 5.5's chunking already
                    # used as its Document node's id - threaded through so
                    # store_contract uses this exact id instead of minting a
                    # second one, closing the cross-tenant chunk-mixing gap.
                    contract_id=contract_id,
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
                    processor = DocumentProcessorFactory.create_processor(
                        "full", llm_mgr.get_raw_model_by_name(model)
                    )
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

                # Deferred from Step 5.5 (see the real sequencing bug
                # explained there): recorded here, after
                # DocumentServiceFactory.create_service(...).
                # process_pdf_upload's own internal
                # workflow_tracker.start_workflow() reset has already run,
                # so this entry survives into the same response the real
                # PDF Processing Agent entry does - with its own real,
                # captured start/end timestamps from when chunking
                # actually ran, not "now."
                if chunking_track_status is not None:
                    chunking_execution = workflow_tracker.start_agent(
                        "Chunking Agent",
                        "Split document into quality-scored chunks with strategy selection and fallback",
                        f"Document ID: {contract_id}, {len(full_text)} chars"
                    )
                    chunking_execution.start_time = chunking_track_start
                    if chunking_track_status == "success":
                        workflow_tracker.complete_agent(chunking_execution, chunking_track_summary)
                    else:
                        workflow_tracker.error_agent(chunking_execution, chunking_track_summary)
                    chunking_execution.end_time = chunking_track_end
                    chunking_execution.processing_time_ms = int(
                        (chunking_execution.end_time - chunking_execution.start_time).total_seconds() * 1000
                    )
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
                    tenant_id=tenant_id,
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

            # Record the real Contract.file_id on the Document node written
            # during Step 5.5 - chunking ran before this id existed, so it
            # used a filename-derived placeholder (chunking_document_id).
            # Best-effort: never let a failure here affect the real upload
            # response, matching this route's established discipline for
            # every other non-critical side effect.
            if async_chunking_succeeded and contract_id:
                try:
                    from backend.infrastructure.chunking.storage_service import ChunkingStorageService
                    await ChunkingStorageService().link_document_to_contract(chunking_document_id, contract_id)
                except Exception as link_error:
                    logger.warning(f"Failed to link document to contract {contract_id}: {link_error}")

            if contract_id:
                metadata_rows = repo.graph.query(
                    "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
                    "SET c.source_hash = $source_hash, c.lifecycle_status = 'ACTIVE' "
                    "RETURN c.file_id AS contract_id",
                    {
                        "contract_id": contract_id,
                        "tenant_id": tenant_id,
                        "source_hash": source_hash,
                    },
                )
                if not metadata_rows:
                    raise RuntimeError("Uploaded contract metadata could not be finalized")

                # Retain the original only after the Contract row and exact
                # source hash are durable. The storage service encrypts bytes
                # with tenant/contract-bound AAD and exposes no filesystem path.
                from backend.application.services.pdf_provenance_service import PdfProvenanceService

                PdfProvenanceService().persist_source(
                    contract_id=contract_id,
                    tenant_id=tenant_id,
                    filename=file.filename,
                    pdf_bytes=file_content,
                    extraction=page_extraction,
                    source_hash=source_hash,
                )

            # Log successful upload
            audit_logger.log_event(
                event_type=AuditEventType.DOCUMENT_UPLOAD,
                resource_id=contract_id or file.filename,
                action="upload_completed",
                tenant_id=tenant_id,
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
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
        finally:
            # Cleanup temp file unconditionally (P3 item 21) - previously
            # only ran here on the exception path. The enable_enhanced=True
            # branch (DocumentProcessorFactory) never cleans up its own
            # file_path, so the raw uploaded PDF was left on disk
            # indefinitely on every successful enhanced-processing upload.
            # (The non-enhanced branch already cleans up internally via
            # DocumentProcessingService._cleanup_file - this is a no-op
            # there since the file no longer exists by this point.)
            if 'temp_path' in locals() and temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                    logger.info(f"Cleaned up temp file: {temp_path}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup temp file: {cleanup_error}")
            logger.info(f"=== UPLOAD END: {file.filename if file else 'unknown'} ===")


@router.get("/{contract_id}/source")
async def get_contract_source_pdf(
    contract_id: str,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Return one active tenant-owned original PDF without exposing its path."""
    from backend.application.services.pdf_provenance_service import PdfProvenanceService
    from backend.infrastructure.pdf_source_storage import PdfSourceUnavailable
    from backend.infrastructure.contract_repository import Neo4jContractRepository

    service = PdfProvenanceService()
    source = service.source_record(contract_id, identity.tenant_id)
    content = None
    filename = "contract.pdf"

    if source:
        try:
            content = service.storage.read(
                identity.tenant_id,
                contract_id,
                source["storage_key"],
            )
            filename = str(source.get("filename") or "contract.pdf")
        except PdfSourceUnavailable:
            content = None

    if content is None:
        # Fallback to filesystem lookup using contract metadata in Neo4j
        rows = Neo4jContractRepository().graph.query(
            "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
            "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
            "RETURN coalesce(c.filename, c.source_filename, c.file_id) AS filename, c.file_path AS file_path",
            {"contract_id": contract_id, "tenant_id": identity.tenant_id},
        )
        if rows:
            fn = str(rows[0].get("filename") or "")
            fp = str(rows[0].get("file_path") or "")
            if fn:
                filename = fn
            candidates = [
                fp,
                f"data/{fn}",
                f"/var/lib/contract-agent/data/{fn}",
                f"/app/data/{fn}",
                f"/app/data/{contract_id}.pdf",
                f"/tmp/{fn}"
            ]
            for candidate in candidates:
                if candidate and os.path.exists(candidate) and os.path.isfile(candidate):
                    try:
                        with open(candidate, "rb") as f:
                            content = f.read()
                        break
                    except Exception:
                        pass

    if content is None:
        raise HTTPException(status_code=404, detail="Source PDF not found")

    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    disposition = f"inline; filename*=UTF-8''{quote(filename, safe='')}"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{contract_id}")
async def archive_contract(
    contract_id: str,
    identity: TokenIdentity = Depends(requires_permission(Permission.DELETE)),
):
    """Archive one active contract and retain its audit/evidence graph."""
    from backend.infrastructure.contract_repository import Neo4jContractRepository
    from backend.shared.cache.redis_cache import cache

    repo = Neo4jContractRepository()
    existing = repo.graph.query(
        "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
        "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
        "RETURN coalesce(c.filename, c.file_id) AS filename, "
        "c.intelligence_status AS intelligence_status, c.analysis_task_state AS task_state",
        {"contract_id": contract_id, "tenant_id": identity.tenant_id},
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Contract not found")
    row = existing[0]
    if row.get("intelligence_status") == "processing" or row.get("task_state") in {"PENDING", "STARTED"}:
        raise HTTPException(status_code=409, detail="Contract cannot be archived while analysis is running")

    archived = repo.graph.query(
        """
        MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
        WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
        OPTIONAL MATCH (d:Document {contract_id: $contract_id, tenant_id: $tenant_id})
        OPTIONAL MATCH (s:ChatSession {contract_id: $contract_id, tenant_id: $tenant_id})
        WITH c, collect(DISTINCT d) AS documents, collect(DISTINCT s) AS sessions
        SET c.lifecycle_status = 'ARCHIVED', c.archived_at = datetime(), c.archived_by = $actor
        FOREACH (document IN documents |
            SET document.lifecycle_status = 'ARCHIVED', document.archived_at = datetime())
        FOREACH (session IN sessions |
            SET session.archived_at = datetime(), session.archived_reason = 'contract_archived')
        RETURN c.file_id AS contract_id
        """,
        {
            "contract_id": contract_id,
            "tenant_id": identity.tenant_id,
            "actor": identity.username or "authenticated_user",
        },
    )
    if not archived:
        raise HTTPException(status_code=404, detail="Contract not found")

    invalidated = cache.invalidate_tenant_search(identity.tenant_id)
    AuditLogger().log_event(
        event_type=AuditEventType.DOCUMENT_DELETE,
        resource_id=contract_id,
        action="contract_archived",
        user_id=identity.username or "authenticated_user",
        tenant_id=identity.tenant_id,
        metadata={"filename": row.get("filename"), "search_cache_entries_invalidated": invalidated},
    )
    return {
        "contract_id": contract_id,
        "filename": row.get("filename") or contract_id,
        "lifecycle_status": "ARCHIVED",
        "sessions": "archived_and_hidden",
        "physical_data_deleted": False,
    }

@router.post("/upload-stream")
async def upload_pdf_stream(
    file: UploadFile = File(...),
    model: str = Query(default="gemini-2.5-flash", description="LLM model to use for processing"),
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.UPLOAD)),
):
    """
    Upload and process PDF with streaming response
    Similar to existing /run/ endpoint pattern
    """
    tenant_id = identity.tenant_id

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
