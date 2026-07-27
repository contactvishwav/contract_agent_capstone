"""
Clause Extraction Agent
Chain of Responsibility + Strategy Pattern for clause identification
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import re
import logging

from backend.agents.llm_extraction_service import LLMExtractionService
from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

@dataclass
class Clause:
    """Clause data model"""
    content: str
    clause_type: str
    order: int
    start_position: int
    end_position: int
    confidence: float = 1.0
    section_id: Optional[str] = None

class IClauseExtractionStrategy(ABC):
    """Strategy interface for clause extraction"""
    
    @abstractmethod
    def extract_clauses(self, section_content: str, section_id: str) -> List[Clause]:
        """Extract clauses from section content"""
        pass

class RegexClauseExtractor(IClauseExtractionStrategy):
    """Fast regex-based clause extraction"""
    
    def extract_clauses(self, section_content: str, section_id: str) -> List[Clause]:
        """Extract clauses using sentence patterns"""
        clauses = []
        
        # Split by sentences and paragraphs
        sentences = re.split(r'[.!?]+\s+', section_content)
        
        order = 0
        current_pos = 0
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20:  # Minimum clause length
                clause = Clause(
                    content=sentence,
                    clause_type="general",
                    order=order,
                    start_position=current_pos,
                    end_position=current_pos + len(sentence),
                    confidence=0.7,
                    section_id=section_id
                )
                clauses.append(clause)
                order += 1
            
            current_pos += len(sentence) + 2  # +2 for punctuation and space
        
        return clauses

class LLMClauseExtractor(IClauseExtractionStrategy):
    """LLM-based intelligent clause extraction - delegates to LLMExtractionService"""

    def __init__(self, llm):
        self.llm = llm
        self._service = LLMExtractionService(llm)

    def extract_clauses(self, section_content: str, section_id: str) -> List[Clause]:
        """Extract clauses using LLM analysis via the shared extraction service"""
        extracted = self._service.extract_clauses(section_content)
        return [
            Clause(
                content=e.extracted_text,
                clause_type=e.clause_type.value,
                order=i,
                start_position=e.start_offset,
                end_position=e.end_offset,
                confidence=e.confidence,
                section_id=section_id,
            )
            for i, e in enumerate(extracted)
        ]

class ClauseExtractionHandler(ABC):
    """Chain of Responsibility handler for clause processing"""
    
    def __init__(self):
        self.next_handler: Optional['ClauseExtractionHandler'] = None
    
    def set_next(self, handler: 'ClauseExtractionHandler') -> 'ClauseExtractionHandler':
        self.next_handler = handler
        return handler
    
    @abstractmethod
    def handle(self, clause: Clause) -> Clause:
        """Process clause and pass to next handler"""
        pass
    
    def process(self, clause: Clause) -> Clause:
        """Process clause through chain"""
        processed_clause = self.handle(clause)
        
        if self.next_handler:
            return self.next_handler.process(processed_clause)
        
        return processed_clause

class ClauseTypeClassifier(ClauseExtractionHandler):
    """Classify clause types"""
    
    def handle(self, clause: Clause) -> Clause:
        """Classify clause type based on content"""
        content_lower = clause.content.lower()
        
        # Simple classification rules
        if any(word in content_lower for word in ['shall', 'must', 'required', 'obligation']):
            clause.clause_type = "obligation"
        elif any(word in content_lower for word in ['right', 'entitled', 'may']):
            clause.clause_type = "right"
        elif any(word in content_lower for word in ['if', 'unless', 'provided', 'condition']):
            clause.clause_type = "condition"
        elif any(word in content_lower for word in ['means', 'defined', 'definition']):
            clause.clause_type = "definition"
        else:
            clause.clause_type = "general"
        
        return clause

class ClauseConfidenceScorer(ClauseExtractionHandler):
    """Score clause extraction confidence"""
    
    def handle(self, clause: Clause) -> Clause:
        """Calculate confidence score"""
        content = clause.content
        
        # Base confidence
        confidence = 0.5
        
        # Length bonus
        if 50 <= len(content) <= 500:
            confidence += 0.2
        
        # Legal language indicators
        legal_terms = ['shall', 'hereby', 'whereas', 'party', 'agreement', 'contract']
        legal_count = sum(1 for term in legal_terms if term in content.lower())
        confidence += min(legal_count * 0.1, 0.3)
        
        clause.confidence = min(confidence, 1.0)
        return clause

class ClauseExtractionAgent:
    """Main clause extraction agent"""
    
    def __init__(self, llm, strategy: str = "regex"):
        self.llm = llm
        self.strategy = self._create_strategy(strategy)
        self.processing_chain = self._create_processing_chain()
    
    def _create_strategy(self, strategy_type: str) -> IClauseExtractionStrategy:
        """Factory method for strategy creation"""
        if strategy_type == "regex":
            return RegexClauseExtractor()
        elif strategy_type == "llm":
            return LLMClauseExtractor(self.llm)
        else:
            raise ValueError(f"Unknown strategy: {strategy_type}")
    
    def _create_processing_chain(self) -> ClauseExtractionHandler:
        """Create chain of responsibility for clause processing"""
        classifier = ClauseTypeClassifier()
        scorer = ClauseConfidenceScorer()
        
        classifier.set_next(scorer)
        return classifier
    
    def extract_clauses_from_section(self, section: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract clauses from a section"""
        try:
            section_id = section["section_id"]
            content = section["content"]
            
            # Extract raw clauses
            clauses = self.strategy.extract_clauses(content, section_id)
            
            # Process through chain
            processed_clauses = []
            for clause in clauses:
                processed_clause = self.processing_chain.process(clause)
                
                clause_dict = {
                    "clause_id": f"{section_id}_clause_{processed_clause.order:03d}",
                    "content": processed_clause.content,
                    "clause_type": processed_clause.clause_type,
                    "order": processed_clause.order,
                    "start_position": processed_clause.start_position,
                    "end_position": processed_clause.end_position,
                    "confidence": processed_clause.confidence,
                    "section_id": section_id
                }
                processed_clauses.append(clause_dict)
            
            return processed_clauses
            
        except Exception as e:
            logger.error(f"Clause extraction failed for section {section.get('section_id')}: {e}")
            return []