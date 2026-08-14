from typing import Dict, Any, List
from langchain_text_splitters import RecursiveCharacterTextSplitter
from . import EmbeddingAgent, EmbeddingResult
from backend.embeddings.strategies.factory import EmbeddingFactory
from backend.embeddings.strategies import EmbeddingType

class DocumentEmbeddingAgent(EmbeddingAgent):
    """Agent for processing document-level embeddings"""
    
    def __init__(self):
        self.factory = EmbeddingFactory()
        self.strategy = self.factory.create_strategy(EmbeddingType.DOCUMENT)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=200,
            separators=["\n\n", "\n", ". ", " "]
        )
    
    def process(self, content: str, metadata: Dict[str, Any] = None) -> List[EmbeddingResult]:
        """Process document and create hierarchical embeddings"""
        results = []
        
        # Full document embedding
        doc_embedding = self.strategy.generate_embedding(content, metadata)
        results.append(EmbeddingResult(
            embedding=doc_embedding,
            metadata={**(metadata or {}), "level": "document"},
            content=content[:500] + "..." if len(content) > 500 else content,
            embedding_type="document"
        ))
        
        # Section embeddings (split by numbered section headers or double newlines)
        import re
        raw_sections = re.split(r'\n+(?=\d+\.\s+[A-Z])|\n\n+', content)
        sections = [s.strip() for s in raw_sections if s.strip()]

        for i, section in enumerate(sections):
            if len(section) > 30:  # Process sections with content
                sec_type = self._identify_section_type(section)
                section_embedding = self.strategy.generate_embedding(section, {
                    **(metadata or {}), 
                    "section_index": i,
                    "section_type": sec_type
                })
                results.append(EmbeddingResult(
                    embedding=section_embedding,
                    metadata={
                        **(metadata or {}), 
                        "level": "section",
                        "section_index": i,
                        "section_type": sec_type
                    },
                    content=section,
                    embedding_type="section"
                ))
        
        return results
    
    def _identify_section_type(self, section: str) -> str:
        """Identify section type based on header title and content"""
        section_lower = section.lower()
        first_line = section_lower.split("\n")[0]
        
        # Check first line / title first
        if any(term in first_line for term in ["indemnifi", "indemnity", "hold harmless"]):
            return "indemnification"
        if any(term in first_line for term in ["liability", "damages", "loss"]):
            return "liability"
        if any(term in first_line for term in ["terminat"]):
            return "termination"
        if any(term in first_line for term in ["payment", "fee", "invoice"]):
            return "payment"
        if any(term in first_line for term in ["intellectual", "ip", "patent", "copyright"]):
            return "intellectual_property"
        if any(term in first_line for term in ["confidential"]):
            return "confidentiality"
        if any(term in first_line for term in ["governing law", "jurisdiction"]):
            return "governing_law"

        # Fallback to full section body search
        if any(term in section_lower for term in ["indemnify", "indemnification", "hold harmless"]):
            return "indemnification"
        elif any(term in section_lower for term in ["limitation of liability", "liable", "damages"]):
            return "liability"
        elif any(term in section_lower for term in ["termination", "terminate"]):
            return "termination"
        elif any(term in section_lower for term in ["payment", "invoice", "fee"]):
            return "payment"
        elif any(term in section_lower for term in ["intellectual property", "patent", "copyright"]):
            return "intellectual_property"
        elif any(term in section_lower for term in ["confidential", "non-disclosure"]):
            return "confidentiality"
        elif any(term in section_lower for term in ["governing law", "jurisdiction"]):
            return "governing_law"
        else:
            return "general"