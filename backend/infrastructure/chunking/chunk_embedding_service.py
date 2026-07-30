"""Chunk-level embedding service using Observer pattern."""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

from backend.governance.pii_engine import PIIEngine
from backend.infrastructure.encryption import field_encryptor
from backend.shared.utils.gemini_embedding_service import GeminiEmbeddingService
from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.vector_index_config import CHUNK_EMBEDDING_INDEX, VECTOR_SEARCH_OVERFETCH


@dataclass
class ChunkEmbedding:
    """Chunk embedding data structure."""
    chunk_id: str
    document_id: str
    embedding: List[float]
    chunk_content: str
    chunk_metadata: Dict[str, Any]


class EmbeddingObserver(ABC):
    """Observer interface for embedding generation events."""
    
    @abstractmethod
    async def on_embedding_generated(self, chunk_embedding: ChunkEmbedding) -> None:
        """Handle embedding generation event."""
        pass
    
    @abstractmethod
    async def on_embedding_failed(self, chunk_id: str, error: Exception) -> None:
        """Handle embedding generation failure."""
        pass


class ChunkEmbeddingService:
    """Service for generating and managing chunk-level embeddings."""
    
    def __init__(self):
        self.embedding_service = GeminiEmbeddingService()
        self.graph = graph
        self._observers: List[EmbeddingObserver] = []
    
    def add_observer(self, observer: EmbeddingObserver) -> None:
        """Add an observer for embedding events."""
        self._observers.append(observer)
    
    def remove_observer(self, observer: EmbeddingObserver) -> None:
        """Remove an observer."""
        if observer in self._observers:
            self._observers.remove(observer)
    
    async def _notify_embedding_generated(self, chunk_embedding: ChunkEmbedding) -> None:
        """Notify observers of successful embedding generation."""
        for observer in self._observers:
            try:
                await observer.on_embedding_generated(chunk_embedding)
            except Exception as e:
                print(f"Observer notification failed: {e}")
    
    async def _notify_embedding_failed(self, chunk_id: str, error: Exception) -> None:
        """Notify observers of embedding generation failure."""
        for observer in self._observers:
            try:
                await observer.on_embedding_failed(chunk_id, error)
            except Exception as e:
                print(f"Observer notification failed: {e}")
    
    async def generate_chunk_embeddings(self, chunks: List[Dict[str, Any]], 
                                      document_id: str) -> List[ChunkEmbedding]:
        """Generate embeddings for all chunks in a document."""
        chunk_embeddings = []
        
        # Process chunks in batches to avoid rate limits
        batch_size = 5
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            batch_results = await self._process_chunk_batch(batch, document_id)
            chunk_embeddings.extend(batch_results)
            
            # Small delay between batches
            if i + batch_size < len(chunks):
                await asyncio.sleep(0.5)
        
        return chunk_embeddings
    
    async def _process_chunk_batch(self, chunks: List[Dict[str, Any]], 
                                 document_id: str) -> List[ChunkEmbedding]:
        """Process a batch of chunks for embedding generation."""
        tasks = []
        for chunk in chunks:
            task = self._generate_single_chunk_embedding(chunk, document_id)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        chunk_embeddings = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                chunk_id = f"{document_id}_chunk_{i}"
                await self._notify_embedding_failed(chunk_id, result)
            else:
                chunk_embeddings.append(result)
                await self._notify_embedding_generated(result)
        
        return chunk_embeddings
    
    async def _generate_single_chunk_embedding(self, chunk: Dict[str, Any], 
                                             document_id: str) -> ChunkEmbedding:
        """Generate embedding for a single chunk."""
        chunk_content = chunk['content']
        chunk_id = f"{document_id}_chunk_{chunk.get('chunk_index', 0)}"
        
        try:
            # Generate embedding using Gemini service
            # Use generate_embedding_async if available, otherwise fallback to sync in thread
            if hasattr(self.embedding_service, 'generate_embedding_async'):
                embedding = await self.embedding_service.generate_embedding_async(chunk_content)
            else:
                import asyncio
                embedding = await asyncio.to_thread(self.embedding_service.embed_query, chunk_content)
            
            # Create chunk embedding object
            chunk_embedding = ChunkEmbedding(
                chunk_id=chunk_id,
                document_id=document_id,
                embedding=embedding,
                chunk_content=chunk_content,
                chunk_metadata={
                    'chunk_type': chunk.get('chunk_type', 'unknown'),
                    'start_position': chunk.get('start_position', 0),
                    'end_position': chunk.get('end_position', 0),
                    'size': chunk.get('size', len(chunk_content)),
                    'has_overlap': chunk.get('has_overlap', False),
                    'overlap_size': chunk.get('overlap_size', 0),
                    'quality_score': chunk.get('quality_score', 0.0)
                }
            )
            
            return chunk_embedding
            
        except Exception as e:
            raise Exception(f"Failed to generate embedding for chunk {chunk_id}: {str(e)}")
    
    async def store_chunk_embeddings(self, chunk_embeddings: List[ChunkEmbedding]) -> bool:
        """Store chunk embeddings in Neo4j database."""
        try:
            for chunk_embedding in chunk_embeddings:
                # Create chunk node with embedding
                query = """
                MATCH (d:Document {id: $document_id})
                CREATE (c:Chunk {
                    id: $chunk_id,
                    content: $content,
                    chunk_type: $chunk_type,
                    start_position: $start_position,
                    end_position: $end_position,
                    size: $size,
                    has_overlap: $has_overlap,
                    overlap_size: $overlap_size,
                    quality_score: $quality_score,
                    embedding: $embedding
                })
                CREATE (d)-[:HAS_CHUNK]->(c)
                """

                # Redact-then-encrypt before persistence (P3 item 21).
                redacted_content = PIIEngine.redact(chunk_embedding.chunk_content)
                encrypted_content = field_encryptor.encrypt(redacted_content)

                self.graph.query(query, {
                    'document_id': chunk_embedding.document_id,
                    'chunk_id': chunk_embedding.chunk_id,
                    'content': encrypted_content,
                    'chunk_type': chunk_embedding.chunk_metadata.get('chunk_type', 'unknown'),
                    'start_position': chunk_embedding.chunk_metadata.get('start_position', 0),
                    'end_position': chunk_embedding.chunk_metadata.get('end_position', 0),
                    'size': chunk_embedding.chunk_metadata.get('size', 0),
                    'has_overlap': chunk_embedding.chunk_metadata.get('has_overlap', False),
                    'overlap_size': chunk_embedding.chunk_metadata.get('overlap_size', 0),
                    'quality_score': chunk_embedding.chunk_metadata.get('quality_score', 0.0),
                    'embedding': chunk_embedding.embedding
                })
            
            return True
            
        except Exception as e:
            print(f"Failed to store chunk embeddings: {e}")
            return False
    
    async def search_similar_chunks(self, query_text: str, document_id: Optional[str] = None,
                                  limit: int = 10, similarity_threshold: float = 0.7) -> List[Dict[str, Any]]:
        """Search for similar chunks using vector similarity."""
        try:
            # Generate embedding for query
            query_embedding = await self.embedding_service.generate_embedding(query_text)
            
            # Build Neo4j query - queries the vector index for candidates
            # instead of scoring every Chunk node.
            if document_id:
                cypher_query = f"""
                CALL db.index.vector.queryNodes('{CHUNK_EMBEDDING_INDEX}', $k, $query_embedding)
                YIELD node AS c, score AS similarity
                MATCH (d:Document {{id: $document_id}})-[:HAS_CHUNK]->(c)
                WHERE similarity >= $threshold
                RETURN c.id as chunk_id, c.content as content, c.chunk_type as chunk_type,
                       c.start_position as start_position, c.end_position as end_position,
                       similarity
                ORDER BY similarity DESC
                LIMIT $limit
                """
                params = {
                    'document_id': document_id,
                    'query_embedding': query_embedding,
                    'threshold': similarity_threshold,
                    'limit': limit,
                    'k': VECTOR_SEARCH_OVERFETCH,
                }
            else:
                cypher_query = f"""
                CALL db.index.vector.queryNodes('{CHUNK_EMBEDDING_INDEX}', $limit, $query_embedding)
                YIELD node AS c, score AS similarity
                WHERE similarity >= $threshold
                RETURN c.id as chunk_id, c.content as content, c.chunk_type as chunk_type,
                       c.start_position as start_position, c.end_position as end_position,
                       similarity
                ORDER BY similarity DESC
                LIMIT $limit
                """
                params = {
                    'query_embedding': query_embedding,
                    'threshold': similarity_threshold,
                    'limit': limit
                }
            
            result = self.graph.query(cypher_query, params)
            
            similar_chunks = []
            for record in result:
                similar_chunks.append({
                    'chunk_id': record['chunk_id'],
                    'content': field_encryptor.decrypt(record['content'] or ""),
                    'chunk_type': record['chunk_type'],
                    'start_position': record['start_position'],
                    'end_position': record['end_position'],
                    'similarity_score': record['similarity']
                })
            
            return similar_chunks
                
        except Exception as e:
            print(f"Failed to search similar chunks: {e}")
            return []
    
    async def get_chunk_embeddings_by_document(self, document_id: str) -> List[ChunkEmbedding]:
        """Retrieve all chunk embeddings for a document."""
        try:
            query = """
            MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(c:Chunk)
            RETURN c.id as chunk_id, c.content as content, c.embedding as embedding,
                   c.chunk_type as chunk_type, c.start_position as start_position,
                   c.end_position as end_position, c.size as size,
                   c.has_overlap as has_overlap, c.overlap_size as overlap_size,
                   c.quality_score as quality_score
            """
            
            result = self.graph.query(query, {'document_id': document_id})
            
            chunk_embeddings = []
            for record in result:
                chunk_embedding = ChunkEmbedding(
                    chunk_id=record['chunk_id'],
                    document_id=document_id,
                    embedding=record['embedding'],
                    chunk_content=field_encryptor.decrypt(record['content'] or ""),
                    chunk_metadata={
                        'chunk_type': record['chunk_type'],
                        'start_position': record['start_position'],
                        'end_position': record['end_position'],
                        'size': record['size'],
                        'has_overlap': record['has_overlap'],
                        'overlap_size': record['overlap_size'],
                        'quality_score': record['quality_score']
                    }
                )
                chunk_embeddings.append(chunk_embedding)
            
            return chunk_embeddings
                
        except Exception as e:
            print(f"Failed to retrieve chunk embeddings: {e}")
            return []


class ChunkEmbeddingLogger(EmbeddingObserver):
    """Observer that logs embedding generation events."""
    
    async def on_embedding_generated(self, chunk_embedding: ChunkEmbedding) -> None:
        """Log successful embedding generation."""
        print(f"Generated embedding for chunk {chunk_embedding.chunk_id} "
              f"(size: {len(chunk_embedding.chunk_content)} chars)")
    
    async def on_embedding_failed(self, chunk_id: str, error: Exception) -> None:
        """Log embedding generation failure."""
        print(f"Failed to generate embedding for chunk {chunk_id}: {error}")


class ChunkEmbeddingMetrics(EmbeddingObserver):
    """Observer that tracks embedding generation metrics."""
    
    def __init__(self):
        self.successful_embeddings = 0
        self.failed_embeddings = 0
        self.total_chunks_processed = 0
    
    async def on_embedding_generated(self, chunk_embedding: ChunkEmbedding) -> None:
        """Track successful embedding generation."""
        self.successful_embeddings += 1
        self.total_chunks_processed += 1
    
    async def on_embedding_failed(self, chunk_id: str, error: Exception) -> None:
        """Track embedding generation failure."""
        self.failed_embeddings += 1
        self.total_chunks_processed += 1
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get embedding generation metrics."""
        success_rate = (self.successful_embeddings / max(self.total_chunks_processed, 1)) * 100
        
        return {
            'successful_embeddings': self.successful_embeddings,
            'failed_embeddings': self.failed_embeddings,
            'total_chunks_processed': self.total_chunks_processed,
            'success_rate': success_rate
        }