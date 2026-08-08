from backend.domain.entities import DocumentProcessingRequest
from backend.agents.pdf_processing_agent import PDFAgentFactory
from backend.domain.value_objects import ProcessingResult, ProcessingStatus
from backend.agents.agent_workflow_tracker import workflow_tracker
from backend.embeddings.orchestrator import EmbeddingOrchestrator
from backend.embeddings.validator import EmbeddingValidator
import os
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class DocumentProcessingService:
    """
    Application Service for document processing
    Follows Single Responsibility Principle with structured output
    """
    
    def __init__(self, agent_manager):
        self.agent_manager = agent_manager
        self.pdf_agent_factory = PDFAgentFactory()
        self.embedding_orchestrator = EmbeddingOrchestrator()
        self.embedding_validator = EmbeddingValidator()
    
    async def process_pdf_upload(self, request: DocumentProcessingRequest) -> dict:
        """
        Process uploaded PDF using agent-based workflow with structured output
        """
        
        try:
            logger.info(f"Starting PDF processing for: {request.filename}")
            
            # 1. Validate file exists
            if not os.path.exists(request.file_path):
                raise FileNotFoundError(f"File not found: {request.file_path}")
            
            # 2. Get appropriate LLM model
            model_name = request.processing_options.get("model", "gemini-2.5-flash")
            llm = self._get_llm_for_model(model_name)
            
            # 3. Create PDF processing agent
            pdf_agent = self.pdf_agent_factory.create_agent(llm)
            
            # 4. Process document using agent with structured state
            result = await self._process_with_agent(pdf_agent, request)
            
            # 5. Clean up temporary file
            self._cleanup_file(request.file_path)
            
            logger.info(f"PDF processing completed for: {request.filename}")
            return result
            
        except Exception as e:
            logger.error(f"PDF processing failed for {request.filename}: {e}")
            self._cleanup_file(request.file_path)
            raise
    
    def _get_llm_for_model(self, model_name: str):
        """Get LLM instance - DRY principle"""
        if model_name == "gpt-4o":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model="gpt-4o", temperature=0)
        elif model_name in ["gemini-1.5-pro", "gemini-2.5-flash-exp", "gemini-2.5-flash", "gemini-2.5-flash"]:
            from langchain_google_genai import ChatGoogleGenerativeAI
            model_mapping = {
                "gemini-2.5-flash-exp": "gemini-2.5-flash",
                "gemini-2.5-flash": "gemini-2.5-flash",
                "gemini-2.5-flash": "gemini-2.5-flash",
                "gemini-1.5-pro": "gemini-1.5-pro"
            }
            actual_model = model_mapping.get(model_name, "gemini-2.5-flash")
            # request_timeout/max_retries: see llm_extraction_service.py's
            # get_default_llm - without these this client has no timeout at
            # all and retries a 429 up to 6 times with growing backoff.
            return ChatGoogleGenerativeAI(model=actual_model, temperature=0, request_timeout=120, max_retries=1)
        elif model_name == "sonnet-3.5":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model="claude-3-5-sonnet-latest", temperature=0)
        else:
            raise ValueError(f"Unknown model: {model_name}")
    
    async def _process_with_agent(self, pdf_agent, request: DocumentProcessingRequest) -> dict:
        """Process document using PDF agent with structured output"""
        
        # Start workflow tracking
        workflow_tracker.start_workflow()
        
        # Track PDF processing agent
        execution = workflow_tracker.start_agent(
            "PDF Processing Agent",
            "Extract text and analyze contract structure from PDF",
            f"PDF file: {request.filename}"
        )
        
        # Create initial state. extracted_text is seeded from
        # processing_options["full_text"] when the caller already
        # extracted it (document_upload.py's /upload route always does,
        # before ever reaching here) - a real, confirmed bug found live
        # during a production incident: this was hardcoded to None
        # unconditionally, so extract_text_node below unconditionally
        # re-extracted the SAME file from scratch on every single upload,
        # wasting real CPU-bound work (confirmed in production logs: a
        # second, redundant extraction of the identical file, genuinely
        # re-running PyPDFExtractor a second time) and doubling this
        # request's exposure to the same slow-extraction risk that caused
        # the incident. should_continue already handles a populated
        # extracted_text correctly (routes straight to analyze_contract),
        # so no other change is needed to skip the redundant node.
        initial_state = {
            "file_path": request.file_path,
            "tenant_id": request.tenant_id or "default-tenant",
            "extracted_text": (request.processing_options or {}).get("full_text"),
            "contract_data": None,
            "processing_result": None,
            "messages": [],
            # Threaded through to ContractData -> the stored Contract node,
            # so duplicate-detection can actually match on it - see
            # value_objects.py's ContractData.filename docstring.
            "filename": request.filename,
            # Real, pre-generated UUID-based id - see
            # Neo4jContractRepository.store_contract's contract_id
            # docstring for the real cross-tenant chunk-mixing bug this
            # fixes.
            "contract_id": request.contract_id,
        }
        
        logger.info("Starting PDF agent processing with structured state")
        
        try:
            # Run the agent workflow
            final_state = await pdf_agent.ainvoke(initial_state)
            processing_result = final_state.get("processing_result")
            
            if not processing_result:
                # Fallback: check if we have contract_data but no result
                contract_data = final_state.get("contract_data")
                if contract_data and contract_data.is_contract:
                    workflow_tracker.error_agent(execution, "Processing completed but storage failed")
                    workflow_tracker.complete_workflow()
                    return {
                        "status": "error",
                        "filename": request.filename,
                        "final_result": "Processing completed but storage failed",
                        "contract_id": None
                    }
                workflow_tracker.error_agent(execution, "No processing result returned")
                workflow_tracker.complete_workflow()
                return {
                    "status": "error",
                    "filename": request.filename,
                    "final_result": "No processing result returned",
                    "contract_id": None
                }
            
            # Process multi-level embeddings if contract was successfully stored
            if processing_result and processing_result.contract_id:
                try:
                    # Get extracted text for embedding processing
                    extracted_text = final_state.get("extracted_text", "")
                    if extracted_text:
                        self._process_enhanced_embeddings(
                            processing_result.contract_id, 
                            extracted_text
                        )
                except Exception as e:
                    logger.warning(f"Enhanced embedding processing failed: {e}")
                
                workflow_tracker.complete_agent(execution, f"Contract stored with ID: {processing_result.contract_id}")
            else:
                workflow_tracker.error_agent(execution, "Failed to store contract")
            
            workflow_tracker.complete_workflow()
            
            # Convert structured result to response format
            return {
                "status": processing_result.status.value,
                "filename": request.filename,
                "final_result": processing_result.message or f"Processing {processing_result.status.value}",
                "contract_id": processing_result.contract_id,
                "error": processing_result.error
            }
            
        except Exception as e:
            workflow_tracker.error_agent(execution, f"Processing failed: {str(e)}")
            workflow_tracker.complete_workflow()
            logger.error(f"Agent processing failed: {e}")
            return {
                "status": "error",
                "filename": request.filename,
                "final_result": f"Processing failed: {str(e)}",
                "contract_id": None
            }
    
    def _cleanup_file(self, file_path: str):
        """Clean up temporary uploaded file"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up temporary file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup file {file_path}: {e}")

class DocumentServiceFactory:
    """Factory for creating document processing services"""
    
    @staticmethod
    def create_service(agent_manager):
        """Create document processing service with dependencies"""
        return DocumentProcessingService(agent_manager)