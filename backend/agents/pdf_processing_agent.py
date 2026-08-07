from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import START, StateGraph, END
from backend.agents.pdf_state import PDFProcessingState
from backend.domain.value_objects import ProcessingResult, ProcessingStatus, ContractData
from backend.infrastructure.text_extractors import TextExtractionService, extract_text_async
from backend.infrastructure.contract_analyzer import LLMContractAnalyzer
from backend.infrastructure.contract_repository import Neo4jContractRepository
import logging
import json

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

def get_pdf_processing_agent(llm):
    """
    Create PDF processing agent with proper state management
    """
    
    # Initialize services (Dependency Injection)
    text_extractor = TextExtractionService()
    contract_analyzer = LLMContractAnalyzer(llm)
    contract_repository = Neo4jContractRepository()
    
    async def extract_text_node(state: PDFProcessingState) -> PDFProcessingState:
        """Extract text from PDF - Single Responsibility.

        START always routes here first regardless of state (see the graph
        wiring below), so a caller that already extracted the text (e.g.
        document_upload.py's /upload route, which threads it through
        processing_options["full_text"] -> initial_state["extracted_text"])
        must be honored here, not just in should_continue's routing - a
        real, confirmed bug found live during a production incident:
        this node used to unconditionally re-extract, wasting a second
        full CPU-bound extraction pass on every single upload and
        doubling that request's exposure to slow-extraction risk.

        The real extraction call itself runs via extract_text_async - off
        the event loop, bounded by EXTRACTION_TIMEOUT_SECONDS - not
        TextExtractionService.extract_with_fallback directly. The same
        incident found that a synchronous, CPU-bound call in a request's
        path can block this single-worker backend's entire event loop,
        including its own health check, for the call's full duration.
        """
        if state.get("extracted_text"):
            logger.info(
                f"Reusing already-extracted text ({len(state['extracted_text'])} characters) - skipping re-extraction"
            )
            return state
        try:
            text = await extract_text_async(text_extractor, state["file_path"])
            logger.info(f"Extracted {len(text)} characters")
            return {**state, "extracted_text": text}
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.ERROR,
                error=f"Text extraction failed: {str(e)}"
            )}
    
    def analyze_contract_node(state: PDFProcessingState) -> PDFProcessingState:
        """Analyze contract - Single Responsibility"""
        if not state.get("extracted_text"):
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.ERROR,
                error="No text to analyze"
            )}
        
        try:
            analysis = contract_analyzer.analyze_contract(state["extracted_text"])
            contract_data = ContractData(
                is_contract=analysis["is_contract"],
                confidence_score=analysis["confidence_score"],
                contract_type=analysis["contract_type"],
                summary=analysis["summary"],
                parties=analysis["parties"],
                effective_date=analysis.get("effective_date"),
                end_date=analysis.get("end_date"),
                total_amount=analysis.get("total_amount"),
                governing_law=analysis.get("governing_law"),
                key_terms=analysis.get("key_terms", []),
                full_text=state["extracted_text"]
            )
            logger.info(f"Analysis complete: confidence={contract_data.confidence_score}")
            return {**state, "contract_data": contract_data}
        except Exception as e:
            logger.error(f"Contract analysis failed: {e}")
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.ERROR,
                error=f"Analysis failed: {str(e)}"
            )}
    
    async def store_contract_node(state: PDFProcessingState) -> PDFProcessingState:
        """Store contract - Single Responsibility"""
        contract_data = state.get("contract_data")
        tenant_id = state.get("tenant_id", "default-tenant")
        
        if not contract_data:
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.ERROR,
                error="No contract data to store"
            )}
        
        # Business logic validation
        if not contract_data.is_contract:
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.SKIPPED,
                message="Document is not a contract"
            )}
        
        if contract_data.confidence_score < 0.7:
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.REVIEW_REQUIRED,
                message=f"Low confidence score: {contract_data.confidence_score}"
            )}
        
        try:
            # Convert to dict for repository
            data_dict = {
                "is_contract": contract_data.is_contract,
                "confidence_score": contract_data.confidence_score,
                "contract_type": contract_data.contract_type,
                "summary": contract_data.summary,
                "parties": contract_data.parties,
                "effective_date": contract_data.effective_date,
                "end_date": contract_data.end_date,
                "total_amount": contract_data.total_amount,
                "governing_law": contract_data.governing_law,
                "key_terms": contract_data.key_terms,
                "full_text": contract_data.full_text,
                "tenant_id": tenant_id
            }
            
            contract_id = await contract_repository.store_contract(data_dict, tenant_id)
            logger.info(f"Contract stored with ID: {contract_id}")
            
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.SUCCESS,
                contract_id=contract_id,
                message=f"Contract stored successfully"
            )}
        except Exception as e:
            logger.error(f"Storage failed: {e}")
            return {**state, "processing_result": ProcessingResult(
                status=ProcessingStatus.ERROR,
                error=f"Storage failed: {str(e)}"
            )}
    
    def should_continue(state: PDFProcessingState) -> str:
        """Routing logic - Open/Closed Principle"""
        if state.get("processing_result"):
            return END
        if not state.get("extracted_text"):
            return "extract_text"
        if not state.get("contract_data"):
            return "analyze_contract"
        return "store_contract"
    
    # Build graph with proper state management
    builder = StateGraph(PDFProcessingState)
    builder.add_node("extract_text", extract_text_node)
    builder.add_node("analyze_contract", analyze_contract_node)
    builder.add_node("store_contract", store_contract_node)
    
    builder.add_edge(START, "extract_text")
    builder.add_conditional_edges("extract_text", should_continue)
    builder.add_conditional_edges("analyze_contract", should_continue)
    builder.add_conditional_edges("store_contract", should_continue)
    
    return builder.compile()

class PDFAgentFactory:
    """Factory for creating PDF processing agents - Factory Pattern"""
    
    @staticmethod
    def create_agent(llm, agent_type: str = "standard"):
        """Create PDF processing agent based on type"""
        
        if agent_type == "standard":
            return get_pdf_processing_agent(llm)
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")
    
    @staticmethod
    def get_available_types():
        """Get list of available agent types"""
        return ["standard"]