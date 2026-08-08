"""Enhanced storage service for chunked documents with embedding integration."""

from typing import List, Dict, Any, Optional
from backend.governance.pii_engine import PIIEngine
from backend.infrastructure.encryption import field_encryptor
from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.vector_index_config import CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT
from backend.infrastructure.chunking.chunk_embedding_service import ChunkEmbeddingService
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


class ChunkingStorageService:
    """Enhanced service for storing and retrieving chunked documents with embeddings."""
    
    def __init__(self):
        self.graph = graph
        self.chunk_embedding_service = ChunkEmbeddingService()
    
    async def store_chunks(self, document_id: str, chunks: List[Dict[str, Any]],
                         metadata: Dict[str, Any] = None, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Store chunks in Neo4j database with enhanced metadata.

        tenant_id: real, confirmed bug found live - this Document node
        never got a tenant_id property at all, but every real chunk-level
        search (chunk_embedding_service.py's search_similar_chunks, and
        the actually-reachable one, enhanced_contract_search_tool.py's
        _search_chunks, both used by Contract Chat) filters on
        `d.tenant_id = $tenant_id` - so chunk-level search was silently
        dead for every tenant, always, on this primary/async chunking
        path (backend/agents/chunking_agent.py's ChunkingAgent.
        process_document, run on every real PDF upload). Required, not
        optional, despite the default - a caller passing tenant_id=None
        would just reproduce the same bug under a different name; the
        default exists only so this doesn't hard-crash existing direct
        callers (e.g. tests) that haven't been updated, not because
        skipping it is ever correct for a real upload.
        """
        try:
            # First, ensure document exists with enhanced metadata
            doc_query = """
            MERGE (d:Document {id: $document_id})
            SET d.chunk_count = $chunk_count,
                d.chunking_metadata = $metadata,
                d.last_chunked = datetime(),
                d.chunking_strategy = $strategy,
                d.avg_chunk_size = $avg_chunk_size,
                d.total_chunks = $total_chunks,
                d.tenant_id = $tenant_id
            """

            # Calculate average chunk size
            avg_chunk_size = sum(chunk.get('size', len(chunk['content'])) for chunk in chunks) / len(chunks)
            strategy_used = chunks[0].get('chunk_type', 'unknown') if chunks else 'unknown'

            import json
            self.graph.query(doc_query, {
                'document_id': document_id,
                'chunk_count': len(chunks),
                'metadata': json.dumps(metadata or {}),
                'strategy': strategy_used,
                'avg_chunk_size': avg_chunk_size,
                'total_chunks': len(chunks),
                'tenant_id': tenant_id
            })
                
            # Store each chunk with enhanced properties
            for chunk in chunks:
                chunk_query = """
                MATCH (d:Document {id: $document_id})
                CREATE (c:Chunk {
                    id: $chunk_id,
                    content: $content,
                    start_position: $start_position,
                    end_position: $end_position,
                    chunk_type: $chunk_type,
                    size: $size,
                    chunk_index: $chunk_index,
                    quality_score: $quality_score,
                    has_overlap: $has_overlap,
                    overlap_size: $overlap_size,
                    embedding_ready: $embedding_ready,
                    created_at: datetime(),
                    parent_section: $parent_section,
                    clause_count: $clause_count
                })
                CREATE (d)-[:HAS_CHUNK {index: $chunk_index}]->(c)
                """
                
                chunk_id = f"{document_id}_chunk_{chunk.get('chunk_index', 0)}"

                # Redact-then-encrypt before persistence, matching
                # Neo4jContractRepository.store_contract's treatment of
                # Contract.full_text (P3 item 21 follow-up).
                redacted_content = PIIEngine.redact(chunk['content'])
                encrypted_content = field_encryptor.encrypt(redacted_content)

                self.graph.query(chunk_query, {
                    'document_id': document_id,
                    'chunk_id': chunk_id,
                    'content': encrypted_content,
                    'start_position': chunk.get('start_position', 0),
                    'end_position': chunk.get('end_position', 0),
                    'chunk_type': chunk.get('chunk_type', 'unknown'),
                    'size': chunk.get('size', len(chunk['content'])),
                    'chunk_index': chunk.get('chunk_index', 0),
                    'quality_score': chunk.get('quality_score', 0.0),
                    'has_overlap': chunk.get('has_overlap', False),
                    'overlap_size': chunk.get('overlap_size', 0),
                    'embedding_ready': chunk.get('embedding_ready', True),
                    'parent_section': chunk.get('parent_section', ''),
                    'clause_count': chunk.get('clause_count', 0)
                })
            
            return {
                'success': True,
                'document_id': document_id,
                'chunks_stored': len(chunks),
                'avg_chunk_size': avg_chunk_size,
                'strategy_used': strategy_used,
                'message': f'Successfully stored {len(chunks)} chunks for document {document_id}'
            }
            
        except Exception as e:
            logger.error(f"Failed to store chunks for document {document_id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to store chunks for document {document_id}'
            }

    async def link_document_to_contract(self, document_id: str, contract_id: str) -> None:
        """Record the real Contract.file_id on this Document node, once
        known - chunking (Step 5.5 of document_upload.py) runs before the
        real Contract node exists (Neo4jContractRepository.store_contract
        generates its UUID-based file_id later, in Step 7), so document_id
        here is a filename-derived placeholder, structurally unrelated to
        the real file_id. No real caller queries chunks scoped to a
        specific contract_id today (the only actually-reachable chunk
        search, enhanced_contract_search_tool.py's _search_chunks, is
        tenant-wide only) - this is forward-looking, cheap to add now
        while the real id is available, rather than unrecoverable later."""
        try:
            self.graph.query(
                "MATCH (d:Document {id: $document_id}) SET d.contract_id = $contract_id",
                {"document_id": document_id, "contract_id": contract_id},
            )
        except Exception as e:
            logger.warning(f"Failed to link document {document_id} to contract {contract_id}: {e}")

    async def get_chunks(self, document_id: str, include_embeddings: bool = False) -> List[Dict[str, Any]]:
        """Retrieve all chunks for a document with optional embeddings."""
        try:
            if include_embeddings:
                query = """
                MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(c:Chunk)
                RETURN c.id as chunk_id, c.content as content, 
                       c.start_position as start_position, c.end_position as end_position,
                       c.chunk_type as chunk_type, c.size as size,
                       c.chunk_index as chunk_index, c.quality_score as quality_score,
                       c.has_overlap as has_overlap, c.overlap_size as overlap_size,
                       c.embedding as embedding, c.embedding_ready as embedding_ready,
                       c.parent_section as parent_section, c.clause_count as clause_count
                ORDER BY c.chunk_index
                """
            else:
                query = """
                MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(c:Chunk)
                RETURN c.id as chunk_id, c.content as content, 
                       c.start_position as start_position, c.end_position as end_position,
                       c.chunk_type as chunk_type, c.size as size,
                       c.chunk_index as chunk_index, c.quality_score as quality_score,
                       c.has_overlap as has_overlap, c.overlap_size as overlap_size,
                       c.embedding_ready as embedding_ready,
                       c.parent_section as parent_section, c.clause_count as clause_count
                ORDER BY c.chunk_index
                """
            
            result = self.graph.query(query, {'document_id': document_id})
                
            chunks = []
            for record in result:
                chunk_data = {
                    'chunk_id': record['chunk_id'],
                    'content': field_encryptor.decrypt(record['content'] or ""),
                    'start_position': record['start_position'],
                    'end_position': record['end_position'],
                    'chunk_type': record['chunk_type'],
                    'size': record['size'],
                    'chunk_index': record['chunk_index'],
                    'quality_score': record['quality_score'],
                    'has_overlap': record['has_overlap'],
                    'overlap_size': record['overlap_size'],
                    'embedding_ready': record['embedding_ready'],
                    'parent_section': record['parent_section'],
                    'clause_count': record['clause_count']
                }
                
                if include_embeddings and 'embedding' in record:
                    chunk_data['embedding'] = record['embedding']
                
                chunks.append(chunk_data)
            
            return chunks
                
        except Exception as e:
            logger.error(f"Failed to retrieve chunks for document {document_id}: {e}")
            return []
    
    async def search_chunks(self, query: str, tenant_id: str, document_id: Optional[str] = None,
                          limit: int = 10, use_embeddings: bool = True) -> List[Dict[str, Any]]:
        """Enhanced search chunks with semantic capabilities by default."""
        try:
            # Always try semantic search first for better results
            if use_embeddings:
                semantic_results = await self.chunk_embedding_service.search_similar_chunks(
                    query, tenant_id, document_id, limit, similarity_threshold=0.7
                )

                if semantic_results:
                    return semantic_results

            # Fallback to enhanced text search. NOTE: _text_search_chunks/
            # _basic_text_search below do not take tenant_id and are not
            # tenant-scoped - a separate, pre-existing gap out of scope for
            # this fix (only search_similar_chunks was in scope), flagged
            # here rather than silently left inconsistent.
            return await self._text_search_chunks(query, document_id, limit)
                
        except Exception as e:
            logger.error(f"Failed to search chunks: {e}")
            # Final fallback to basic text search
            return await self._basic_text_search(query, document_id, limit)
    
    async def _text_search_chunks(self, query: str, document_id: Optional[str] = None,
                                limit: int = 10) -> List[Dict[str, Any]]:
        """
        Text-based chunk search fallback. Chunk.content is encrypted at rest
        (P3 item 21 follow-up), so Cypher can no longer CONTAINS-match it
        directly - a bounded, quality-ordered candidate set is fetched
        instead, decrypted, and matched in Python.
        """
        try:
            if document_id:
                cypher_query = """
                MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(c:Chunk)
                RETURN c.id as chunk_id, c.content as content,
                       c.chunk_type as chunk_type, c.quality_score as quality_score,
                       c.start_position as start_position, c.end_position as end_position
                ORDER BY c.quality_score DESC, c.chunk_index ASC
                LIMIT $candidate_limit
                """
                params = {'document_id': document_id, 'candidate_limit': CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT}
            else:
                cypher_query = """
                MATCH (c:Chunk)
                RETURN c.id as chunk_id, c.content as content,
                       c.chunk_type as chunk_type, c.quality_score as quality_score,
                       c.start_position as start_position, c.end_position as end_position
                ORDER BY c.quality_score DESC
                LIMIT $candidate_limit
                """
                params = {'candidate_limit': CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT}

            result = self.graph.query(cypher_query, params)

            chunks = []
            for record in result:
                decrypted_content = field_encryptor.decrypt(record['content'] or "")
                if query not in decrypted_content:
                    continue
                chunks.append({
                    'chunk_id': record['chunk_id'],
                    'content': decrypted_content,
                    'chunk_type': record['chunk_type'],
                    'quality_score': record['quality_score'],
                    'start_position': record['start_position'],
                    'end_position': record['end_position'],
                    'similarity_score': 1.0  # Default for text search
                })
                if len(chunks) >= limit:
                    break

            return chunks

        except Exception as e:
            logger.error(f"Failed to perform text search: {e}")
            return []

    async def _basic_text_search(self, query: str, document_id: Optional[str] = None,
                               limit: int = 10) -> List[Dict[str, Any]]:
        """Basic text search as final fallback - same encrypted-content
        treatment as _text_search_chunks, case-insensitive match."""
        try:
            if document_id:
                cypher_query = """
                MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(c:Chunk)
                RETURN c.id as chunk_id, c.content as content,
                       c.chunk_type as chunk_type, c.quality_score as quality_score
                ORDER BY c.quality_score DESC
                LIMIT $candidate_limit
                """
                params = {'document_id': document_id, 'candidate_limit': CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT}
            else:
                cypher_query = """
                MATCH (c:Chunk)
                RETURN c.id as chunk_id, c.content as content,
                       c.chunk_type as chunk_type, c.quality_score as quality_score
                ORDER BY c.quality_score DESC
                LIMIT $candidate_limit
                """
                params = {'candidate_limit': CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT}

            result = self.graph.query(cypher_query, params)

            query_lower = query.lower()
            chunks = []
            for record in result:
                decrypted_content = field_encryptor.decrypt(record['content'] or "")
                if query_lower not in decrypted_content.lower():
                    continue
                chunks.append({
                    'chunk_id': record['chunk_id'],
                    'content': decrypted_content,
                    'chunk_type': record['chunk_type'],
                    'quality_score': record['quality_score'],
                    'similarity_score': 0.8,  # Default for text search
                    'search_type': 'basic_text'
                })
                if len(chunks) >= limit:
                    break

            return chunks

        except Exception as e:
            logger.error(f"Basic text search failed: {e}")
            return []
    
    async def get_chunk_statistics(self, document_id: str) -> Dict[str, Any]:
        """Get comprehensive statistics about chunks for a document."""
        try:
            query = """
            MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(c:Chunk)
            RETURN 
                count(c) as total_chunks,
                avg(c.size) as avg_chunk_size,
                min(c.size) as min_chunk_size,
                max(c.size) as max_chunk_size,
                avg(c.quality_score) as avg_quality_score,
                sum(case when c.has_overlap then 1 else 0 end) as chunks_with_overlap,
                collect(distinct c.chunk_type) as chunk_types,
                sum(case when c.embedding_ready then 1 else 0 end) as embedding_ready_chunks
            """
            
            result = self.graph.query(query, {'document_id': document_id})
            record = result[0] if result else None
            
            if record:
                return {
                    'total_chunks': record['total_chunks'],
                    'avg_chunk_size': record['avg_chunk_size'],
                    'min_chunk_size': record['min_chunk_size'],
                    'max_chunk_size': record['max_chunk_size'],
                    'avg_quality_score': record['avg_quality_score'],
                    'chunks_with_overlap': record['chunks_with_overlap'],
                    'chunk_types': record['chunk_types'],
                    'embedding_ready_chunks': record['embedding_ready_chunks'],
                    'embedding_coverage': record['embedding_ready_chunks'] / max(record['total_chunks'], 1)
                }
            else:
                return {'error': 'No chunks found for document'}
                    
        except Exception as e:
            logger.error(f"Failed to get chunk statistics for document {document_id}: {e}")
            return {'error': str(e)}
    
    async def delete_chunks(self, document_id: str) -> Dict[str, Any]:
        """Delete all chunks for a document including embeddings."""
        try:
            query = """
            MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(c:Chunk)
            DETACH DELETE c
            """
            
            self.graph.query(query, {'document_id': document_id})
            
            return {
                'success': True,
                'message': f'Successfully deleted chunks for document {document_id}'
            }
                
        except Exception as e:
            logger.error(f"Failed to delete chunks for document {document_id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to delete chunks for document {document_id}'
            }


# Backward compatibility class
class ChunkStorageService(ChunkingStorageService):
    """Backward compatibility wrapper."""
    
    def store_chunks(self, contract_id: str, chunks: List, tenant_id: str = "demo_tenant_1") -> List[str]:
        """Synchronous backward compatibility method handling both objects and dictionaries."""
        chunk_ids = []
        
        def safe_get(chunk_item: Any, attr: str, default: Any = None) -> Any:
            """Safely extract from dict or object following DRY principles."""
            if isinstance(chunk_item, dict):
                return chunk_item.get(attr, default)
            return getattr(chunk_item, attr, default)
            
        for i, chunk in enumerate(chunks):
            chunk_id = f"{contract_id}_chunk_{i}"
            
            try:
                # Extract properties safely regardless of input type
                content = safe_get(chunk, 'content', '')
                chunk_type = safe_get(chunk, 'chunk_type', safe_get(chunk, 'type', 'sentence'))
                confidence = safe_get(chunk, 'confidence', safe_get(chunk, 'quality_score', 1.0))

                # Redact-then-encrypt before persistence, matching the rest
                # of this file's Chunk.content treatment (P3 item 21).
                redacted_content = PIIEngine.redact(content)
                encrypted_content = field_encryptor.encrypt(redacted_content)

                self.graph.query("""
                    MERGE (dc:DocumentChunk {chunk_id: $chunk_id})
                    SET dc.contract_id = $contract_id,
                        dc.tenant_id = $tenant_id,
                        dc.chunk_order = $order,
                        dc.chunk_size = $size,
                        dc.chunk_type = $type,
                        dc.content = $content,
                        dc.confidence = $confidence,
                        dc.created_at = datetime()
                    
                    WITH dc
                    MATCH (c:Contract)
                    WHERE c.file_id CONTAINS $contract_id
                    MERGE (c)-[:CONTAINS_CHUNK]->(dc)
                """, {
                    "chunk_id": chunk_id,
                    "contract_id": contract_id,
                    "tenant_id": tenant_id,
                    "order": i,
                    "size": len(content),
                    "type": chunk_type,
                    "content": encrypted_content,
                    "confidence": float(confidence)
                })
                
                chunk_ids.append(chunk_id)
                
            except Exception as e:
                logger.error(f"Failed to store chunk {chunk_id}: {e}", exc_info=True)
        
        return chunk_ids