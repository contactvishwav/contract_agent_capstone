"""Chunk-level embedding service using Observer pattern."""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

import hashlib
import json

from backend.infrastructure.encryption import field_encryptor
from backend.shared.utils.gemini_embedding_service import GeminiEmbeddingService
from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.vector_index_config import CHUNK_EMBEDDING_INDEX, VECTOR_SEARCH_OVERFETCH
from backend.shared.cache.redis_cache import cache
from backend.shared.config.phase3_config import Phase3Config


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
        """Attach embeddings onto already-persisted Chunk nodes.

        Real, confirmed bug found live: this used to MATCH (d:Document
        {id: $document_id}) and CREATE a brand-new Chunk node with the
        embedding attached - but on the real, primary chunking pipeline
        (ChunkingAgent.process_document -> ChunkingOrchestrator.
        execute_chunking), that MATCH ran *before* the Document node
        existed (ChunkingStorageService.store_chunks, which actually
        MERGEs the Document and CREATEs the real Chunk nodes with all
        their real metadata, only runs afterward, once execute_chunking
        already returned). The MATCH silently found nothing, so the
        CREATE inside it never fired - no exception, nothing logged,
        embeddings just never persisted. Confirmed directly in production
        Neo4j: every real Chunk node had embedding_ready: true but
        embedding: null, invisible to chunk_embedding_vector_index.

        Fixed by inverting the dependency: this now runs strictly after
        the real Chunk nodes already exist (see ChunkingAgent.
        process_document's post-storage embedding step) and MATCHes the
        Chunk directly by id, just SETting the embedding vector - it no
        longer creates a node, duplicates content, or re-derives
        metadata that ChunkingStorageService.store_chunks already wrote
        (and already redacted+encrypted) when it created the chunk.
        """
        try:
            for chunk_embedding in chunk_embeddings:
                query = """
                MATCH (c:Chunk {id: $chunk_id})
                SET c.embedding = $embedding
                """

                self.graph.query(query, {
                    'chunk_id': chunk_embedding.chunk_id,
                    'embedding': chunk_embedding.embedding
                })

            return True

        except Exception as e:
            print(f"Failed to store chunk embeddings: {e}")
            return False
    
    async def search_similar_chunks(self, query_text: str, tenant_id: str, document_id: Optional[str] = None,
                                  limit: int = 10, similarity_threshold: float = 0.7) -> List[Dict[str, Any]]:
        """Search for similar chunks using vector similarity, scoped to tenant_id.

        Chunk nodes carry no tenant_id property of their own - tenant
        scoping goes through the Document they're attached to (same
        d.tenant_id join pattern already used for the Chunk-level search in
        enhanced_contract_search_tool.py). Found and fixed as a real gap:
        this method previously took no tenant_id at all, so an omitted
        document_id searched every tenant's chunks in the database.
        tenant_id is required (no default), matching this codebase's
        established "reject rather than silently default" convention (P1)
        for anything tenant-scoped.

        Caches the real vector-index (db.index.vector.queryNodes) retrieval
        via Redis - same infra/TTL bucket ("vector_search") as
        enhanced_contract_search_tool.py's get_contracts_multi_level. Keyed
        on the query text itself rather than its embedding, since embedding
        identical text is deterministic - a cache hit skips both the
        embedding-generation call and the graph query. tenant_id is part of
        the key - without it, one tenant's cached results could be served
        back for another tenant's identical-looking query, a confidentiality
        bug in the cache layer even with the query itself now tenant-scoped.
        """
        cache_key_raw = json.dumps({'tenant_id': tenant_id, 'query_text': query_text, 'document_id': document_id, 'limit': limit, 'similarity_threshold': similarity_threshold}, sort_keys=True)
        cache_key = f"vector_search:{tenant_id}:chunk:{hashlib.sha256(cache_key_raw.encode()).hexdigest()}"
        if Phase3Config.CACHE_ENABLED:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        try:
            # Generate embedding for query
            query_embedding = await self.embedding_service.generate_embedding(query_text)

            # Build Neo4j query - queries the vector index for overfetched
            # candidates (instead of scoring every Chunk node), then filters
            # to this tenant's own Document(s) afterward - a vector index
            # query has no way to pre-filter by tenant before ranking
            # globally (same overfetch-then-filter tradeoff already
            # documented for VECTOR_SEARCH_OVERFETCH elsewhere).
            if document_id:
                cypher_query = f"""
                CALL db.index.vector.queryNodes('{CHUNK_EMBEDDING_INDEX}', $k, $query_embedding)
                YIELD node AS c, score AS similarity
                MATCH (d:Document {{id: $document_id, tenant_id: $tenant_id}})-[:HAS_CHUNK]->(c)
                WHERE similarity >= $threshold
                RETURN c.id as chunk_id, c.content as content, c.chunk_type as chunk_type,
                       c.start_position as start_position, c.end_position as end_position,
                       similarity
                ORDER BY similarity DESC
                LIMIT $limit
                """
                params = {
                    'document_id': document_id,
                    'tenant_id': tenant_id,
                    'query_embedding': query_embedding,
                    'threshold': similarity_threshold,
                    'limit': limit,
                    'k': VECTOR_SEARCH_OVERFETCH,
                }
            else:
                cypher_query = f"""
                CALL db.index.vector.queryNodes('{CHUNK_EMBEDDING_INDEX}', $k, $query_embedding)
                YIELD node AS c, score AS similarity
                MATCH (d:Document {{tenant_id: $tenant_id}})-[:HAS_CHUNK]->(c)
                WHERE similarity >= $threshold
                RETURN c.id as chunk_id, c.content as content, c.chunk_type as chunk_type,
                       c.start_position as start_position, c.end_position as end_position,
                       similarity
                ORDER BY similarity DESC
                LIMIT $limit
                """
                params = {
                    'tenant_id': tenant_id,
                    'query_embedding': query_embedding,
                    'threshold': similarity_threshold,
                    'limit': limit,
                    'k': VECTOR_SEARCH_OVERFETCH,
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

            if Phase3Config.CACHE_ENABLED:
                cache.set(cache_key, similar_chunks, ttl=Phase3Config.get_cache_ttl("vector_search"))

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
