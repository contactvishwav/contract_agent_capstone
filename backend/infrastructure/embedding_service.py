"""
Embedding Service for Sections and Clauses
Strategy Pattern for different embedding approaches
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import logging

from backend.shared.utils.vector_index_config import SECTION_EMBEDDING_INDEX, CLAUSE_EMBEDDING_INDEX
from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class IEmbeddingStrategy(ABC):
    """Strategy interface for embedding generation"""
    
    @abstractmethod
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text"""
        pass

class GeminiEmbeddingStrategy(IEmbeddingStrategy):
    """Gemini embedding strategy - reuse existing service"""
    
    def __init__(self):
        from backend.shared.utils.gemini_embedding_service import embedding
        self.embedding_service = embedding
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate 1536-dimensional embedding using Gemini"""
        try:
            return self.embedding_service.embed_query(text)
        except Exception as e:
            logger.error(f"Gemini embedding failed: {e}")
            return []

class EmbeddingService:
    """Service for generating and storing embeddings"""
    
    def __init__(self, strategy: str = "gemini"):
        self.strategy = self._create_strategy(strategy)
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        self.repository = Neo4jContractRepository()
    
    def _create_strategy(self, strategy_type: str) -> IEmbeddingStrategy:
        """Factory method for embedding strategy"""
        if strategy_type == "gemini":
            return GeminiEmbeddingStrategy()
        else:
            raise ValueError(f"Unknown embedding strategy: {strategy_type}")
    
    def generate_section_embeddings(self, sections: List[Dict[str, Any]]) -> bool:
        """Generate and store embeddings for sections"""
        try:
            for section in sections:
                # Generate embedding for section content
                content = section["content"]
                if len(content) > 50:  # Only embed substantial content
                    embedding = self.strategy.generate_embedding(content)
                    
                    if embedding:
                        # Store embedding in section node
                        self._store_section_embedding(section["section_id"], embedding)
            
            logger.info(f"Generated embeddings for {len(sections)} sections")
            return True
            
        except Exception as e:
            logger.error(f"Section embedding generation failed: {e}")
            return False
    
    def generate_clause_embeddings(self, clauses: List[Dict[str, Any]]) -> bool:
        """Generate and store embeddings for clauses"""
        try:
            for clause in clauses:
                # Generate embedding for clause content
                content = clause["content"]
                if len(content) > 20:  # Only embed substantial content
                    embedding = self.strategy.generate_embedding(content)
                    
                    if embedding:
                        # Store embedding in clause node
                        self._store_clause_embedding(clause["clause_id"], embedding)
            
            logger.info(f"Generated embeddings for {len(clauses)} clauses")
            return True
            
        except Exception as e:
            logger.error(f"Clause embedding generation failed: {e}")
            return False
    
    def _store_section_embedding(self, section_id: str, embedding: List[float]):
        """Store embedding in section node"""
        query = """
        MATCH (s:Section {section_id: $section_id})
        SET s.embedding = $embedding,
            s.embedding_model = 'gemini-embedding-001',
            s.embedding_dimensions = $dimensions,
            s.embedding_generated_at = datetime()
        """
        
        self.repository.graph.query(query, {
            "section_id": section_id,
            "embedding": embedding,
            "dimensions": len(embedding)
        })
    
    def _store_clause_embedding(self, clause_id: str, embedding: List[float]):
        """Store embedding in clause node"""
        query = """
        MATCH (cl:Clause {clause_id: $clause_id})
        SET cl.embedding = $embedding,
            cl.embedding_model = 'gemini-embedding-001',
            cl.embedding_dimensions = $dimensions,
            cl.embedding_generated_at = datetime()
        """
        
        self.repository.graph.query(query, {
            "clause_id": clause_id,
            "embedding": embedding,
            "dimensions": len(embedding)
        })
    
    def search_similar_sections(self, query_text: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for similar sections using embeddings"""
        try:
            query_embedding = self.strategy.generate_embedding(query_text)
            
            if not query_embedding:
                return []
            
            cypher_query = f"""
            CALL db.index.vector.queryNodes('{SECTION_EMBEDDING_INDEX}', $limit, $query_embedding)
            YIELD node AS s, score AS similarity
            WHERE similarity > 0.8
            RETURN s.section_id as section_id,
                   s.title as title,
                   s.content as content,
                   similarity
            ORDER BY similarity DESC
            LIMIT $limit
            """

            result = self.repository.graph.query(cypher_query, {
                "query_embedding": query_embedding,
                "limit": limit
            })
            
            return [dict(row) for row in result]
            
        except Exception as e:
            logger.error(f"Section similarity search failed: {e}")
            return []
    
    def search_similar_clauses(self, query_text: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for similar clauses using embeddings"""
        try:
            query_embedding = self.strategy.generate_embedding(query_text)
            
            if not query_embedding:
                return []
            
            cypher_query = f"""
            CALL db.index.vector.queryNodes('{CLAUSE_EMBEDDING_INDEX}', $limit, $query_embedding)
            YIELD node AS cl, score AS similarity
            WHERE similarity > 0.8
            RETURN cl.clause_id as clause_id,
                   cl.content as content,
                   cl.clause_type as clause_type,
                   similarity
            ORDER BY similarity DESC
            LIMIT $limit
            """

            result = self.repository.graph.query(cypher_query, {
                "query_embedding": query_embedding,
                "limit": limit
            })
            
            return [dict(row) for row in result]
            
        except Exception as e:
            logger.error(f"Clause similarity search failed: {e}")
            return []