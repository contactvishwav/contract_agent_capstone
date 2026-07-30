from backend.domain.entities import DocumentProcessingRequest
from backend.agents.pdf_processing_agent import PDFAgentFactory
from backend.domain.value_objects import ProcessingResult, ProcessingStatus
from backend.agents.agent_workflow_tracker import workflow_tracker
from backend.embeddings.orchestrator import EmbeddingOrchestrator
from backend.embeddings.validator import EmbeddingValidator
from langchain_neo4j import Neo4jGraph
import os
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class EnhancedDocumentProcessingService:
    """
    Enhanced document processing service with multi-level embeddings
    """
    
    def __init__(self, agent_manager):
        self.agent_manager = agent_manager
        self.pdf_agent_factory = PDFAgentFactory()
        self.embedding_orchestrator = EmbeddingOrchestrator()
        self.embedding_validator = EmbeddingValidator()
        self.graph = Neo4jGraph(
            refresh_schema=False, 
            driver_config={"notifications_min_severity": "OFF"}
        )
    
    def process_pdf_with_embeddings(self, request: DocumentProcessingRequest) -> dict:
        """
        Process uploaded PDF with enhanced multi-level embeddings
        """
        
        try:
            logger.info(f"Starting enhanced PDF processing for: {request.filename}")
            
            # 1. Validate file exists
            if not os.path.exists(request.file_path):
                raise FileNotFoundError(f"File not found: {request.file_path}")
            
            # 2. Get appropriate LLM model
            model_name = request.processing_options.get("model", "gemini-2.5-flash")
            llm = self._get_llm_for_model(model_name)
            
            # 3. Create PDF processing agent
            pdf_agent = self.pdf_agent_factory.create_agent(llm)
            
            # 4. Process document using agent
            result = self._process_with_enhanced_embeddings(pdf_agent, request)
            
            # 5. Clean up temporary file
            self._cleanup_file(request.file_path)
            
            logger.info(f"Enhanced PDF processing completed for: {request.filename}")
            return result
            
        except Exception as e:
            logger.error(f"Enhanced PDF processing failed for {request.filename}: {e}")
            self._cleanup_file(request.file_path)
            raise
    
    def _get_llm_for_model(self, model_name: str):
        """Get LLM instance"""
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
    
    def _process_with_enhanced_embeddings(self, pdf_agent, request: DocumentProcessingRequest) -> dict:
        """Process document with enhanced embeddings"""
        
        # Start workflow tracking
        workflow_tracker.start_workflow()
        
        # Track PDF processing agent
        pdf_execution = workflow_tracker.start_agent(
            "PDF Processing Agent",
            "Extract text and analyze contract structure from PDF",
            f"PDF file: {request.filename}"
        )
        
        # Create initial state
        initial_state = {
            "file_path": request.file_path,
            "extracted_text": None,
            "contract_data": None,
            "processing_result": None,
            "messages": []
        }
        
        logger.info("Starting PDF agent processing with enhanced embeddings")
        
        try:
            # Run the PDF agent workflow
            final_state = pdf_agent.invoke(initial_state)
            processing_result = final_state.get("processing_result")
            extracted_text = final_state.get("extracted_text", "")
            
            if not processing_result or not processing_result.contract_id:
                workflow_tracker.error_agent(pdf_execution, "PDF processing failed")
                workflow_tracker.complete_workflow()
                return {
                    "status": "error",
                    "filename": request.filename,
                    "final_result": "PDF processing failed",
                    "contract_id": None
                }
            
            # Complete PDF processing
            workflow_tracker.complete_agent(pdf_execution, f"Contract stored with ID: {processing_result.contract_id}")
            
            # Process enhanced embeddings
            embedding_success = self._process_enhanced_embeddings(
                processing_result.contract_id, 
                extracted_text,
                request.filename
            )
            
            workflow_tracker.complete_workflow()
            
            # Return enhanced result
            return {
                "status": processing_result.status.value,
                "filename": request.filename,
                "final_result": f"Processing {processing_result.status.value} with enhanced embeddings",
                "contract_id": processing_result.contract_id,
                "enhanced_embeddings": embedding_success,
                "error": processing_result.error
            }
            
        except Exception as e:
            workflow_tracker.error_agent(pdf_execution, f"Processing failed: {str(e)}")
            workflow_tracker.complete_workflow()
            logger.error(f"Enhanced processing failed: {e}")
            return {
                "status": "error",
                "filename": request.filename,
                "final_result": f"Enhanced processing failed: {str(e)}",
                "contract_id": None
            }
    
    def _process_enhanced_embeddings(self, contract_id: str, extracted_text: str, filename: str) -> bool:
        """Process multi-level embeddings for uploaded contract"""
        
        # Track embedding processing
        embedding_execution = workflow_tracker.start_agent(
            "Enhanced Embedding Agent",
            "Generate multi-level embeddings (document, section, clause, relationship)",
            f"Contract ID: {contract_id}"
        )
        
        try:
            logger.info(f"Processing enhanced embeddings for contract: {contract_id}")
            
            if not extracted_text or len(extracted_text.strip()) < 50:
                workflow_tracker.error_agent(embedding_execution, "Insufficient text for embedding processing")
                return False
            
            # Process with orchestrator
            processing_result = self.embedding_orchestrator.process_document(
                content=extracted_text,
                metadata={"file_id": contract_id, "source": "upload", "filename": filename}
            )
            
            # Validate results
            all_embeddings = (
                processing_result.document_embeddings + 
                processing_result.clause_embeddings + 
                processing_result.relationship_embeddings
            )
            
            if not all_embeddings:
                workflow_tracker.error_agent(embedding_execution, "No embeddings generated")
                return False
            
            validation_result = self.embedding_validator.validate_embeddings(all_embeddings)
            
            if not validation_result.is_valid:
                workflow_tracker.error_agent(embedding_execution, f"Validation failed: {validation_result.errors}")
                return False
            
            # Store embeddings in Neo4j
            self._store_enhanced_embeddings(contract_id, processing_result)
            
            workflow_tracker.complete_agent(
                embedding_execution, 
                f"Generated {len(all_embeddings)} embeddings: {len(processing_result.document_embeddings)} doc, {len(processing_result.clause_embeddings)} clause, {len(processing_result.relationship_embeddings)} relationship"
            )
            
            logger.info(f"Enhanced embeddings processed successfully for contract: {contract_id}")
            return True
            
        except Exception as e:
            workflow_tracker.error_agent(embedding_execution, f"Embedding processing failed: {str(e)}")
            logger.error(f"Enhanced embedding processing failed for {contract_id}: {e}")
            return False
    
    def _store_enhanced_embeddings(self, contract_id: str, processing_result):
        """Store enhanced embeddings in Neo4j"""
        
        # Store document embeddings
        for doc_embedding in processing_result.document_embeddings:
            if doc_embedding.metadata.get("level") == "document":
                self.graph.query("""
                    MATCH (c:Contract {file_id: $file_id})
                    SET c.document_embedding = $embedding,
                        c.summary_embedding = $embedding
                """, {
                    "file_id": contract_id,
                    "embedding": doc_embedding.embedding
                })
            
            elif doc_embedding.metadata.get("level") == "section":
                section_id = f"{contract_id}_section_{doc_embedding.metadata.get('section_index', 0)}"
                self.graph.query("""
                    MATCH (c:Contract {file_id: $file_id})
                    MERGE (s:Section {id: $section_id})
                    SET s.section_type = $section_type,
                        s.content = $content,
                        s.embedding = $embedding,
                        s.order = $order
                    MERGE (c)-[:HAS_SECTION]->(s)
                """, {
                    "file_id": contract_id,
                    "section_id": section_id,
                    "section_type": doc_embedding.metadata.get("section_type", "general"),
                    "content": doc_embedding.content,
                    "embedding": doc_embedding.embedding,
                    "order": doc_embedding.metadata.get("section_index", 0)
                })
        
        # Store clause embeddings
        for clause_embedding in processing_result.clause_embeddings:
            clause_id = f"{contract_id}_clause_{clause_embedding.metadata.get('start_position', 0)}"
            self.graph.query("""
                MATCH (c:Contract {file_id: $file_id})
                MERGE (cl:Clause {id: $clause_id})
                SET cl.clause_type = $clause_type,
                    cl.content = $content,
                    cl.embedding = $embedding,
                    cl.confidence = $confidence,
                    cl.start_position = $start_position,
                    cl.end_position = $end_position
                MERGE (c)-[:CONTAINS_CLAUSE]->(cl)
            """, {
                "file_id": contract_id,
                "clause_id": clause_id,
                "clause_type": clause_embedding.metadata.get("clause_type", "unknown"),
                "content": clause_embedding.content,
                "embedding": clause_embedding.embedding,
                "confidence": clause_embedding.metadata.get("confidence", 0.0),
                "start_position": clause_embedding.metadata.get("start_position", 0),
                "end_position": clause_embedding.metadata.get("end_position", 0)
            })
        
        # Store relationship embeddings
        for rel_embedding in processing_result.relationship_embeddings:
            if rel_embedding.metadata.get("relationship_type") == "PARTY_TO":
                party_name = rel_embedding.metadata.get("party_name", "")
                if party_name:
                    self.graph.query("""
                        MATCH (c:Contract {file_id: $file_id})
                        MATCH (c)<-[r:PARTY_TO]-(p:Party {name: $party_name})
                        SET r.embedding = $embedding,
                            r.context = $context
                    """, {
                        "file_id": contract_id,
                        "party_name": party_name,
                        "embedding": rel_embedding.embedding,
                        "context": rel_embedding.content
                    })
    
    def _cleanup_file(self, file_path: str):
        """Clean up temporary uploaded file"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up temporary file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup file {file_path}: {e}")

class EnhancedDocumentServiceFactory:
    """Factory for creating enhanced document processing services"""
    
    @staticmethod
    def create_service(agent_manager):
        """Create enhanced document processing service with dependencies"""
        return EnhancedDocumentProcessingService(agent_manager)