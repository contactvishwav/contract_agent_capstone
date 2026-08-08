"""Enhanced chunking agent with planning, self-reflection, and advanced strategy selection."""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

from backend.infrastructure.chunking.factory import ChunkingFactory
from backend.infrastructure.chunking.storage_service import ChunkingStorageService
from backend.infrastructure.chunking.document_analyzer import analyze_document
from backend.infrastructure.chunking.content_density_analyzer import ContentDensityAnalyzer
from backend.infrastructure.chunking.chunk_embedding_service import ChunkEmbeddingService, ChunkEmbeddingLogger, ChunkEmbeddingMetrics
from backend.shared.utils.gemini_embedding_service import GeminiEmbeddingService

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


@dataclass
class ChunkingPlan:
    """Enhanced plan for chunking a document."""
    strategy_type: str
    chunk_size_range: tuple
    overlap_ratio: float
    expected_chunk_count: int
    reasoning: str
    document_analysis: Dict[str, Any]
    content_metrics: Dict[str, Any]


class ChunkingAgent:
    """Enhanced agent with advanced strategy selection and embedding generation."""
    
    def __init__(self, embedding_service=None):
        self.factory = ChunkingFactory()
        self.storage_service = ChunkingStorageService()
        self.embedding_service = embedding_service or GeminiEmbeddingService()
        self.chunk_embedding_service = ChunkEmbeddingService()
        self.density_analyzer = ContentDensityAnalyzer()
        
        # Add observers for embedding service
        self.embedding_logger = ChunkEmbeddingLogger()
        self.embedding_metrics = ChunkEmbeddingMetrics()
        self.chunk_embedding_service.add_observer(self.embedding_logger)
        self.chunk_embedding_service.add_observer(self.embedding_metrics)
    
    async def process_document(self, document_id: str, content: str,
                             metadata: Dict[str, Any] = None, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Process document using orchestrator for comprehensive chunking.

        tenant_id: threaded through to ChunkingStorageService.store_chunks -
        see its docstring for the real bug this fixes (chunk-level search
        was silently dead for every tenant without it)."""
        from backend.infrastructure.chunking.chunking_orchestrator import ChunkingOrchestrator, ChunkingCommandFactory

        try:
            # Use orchestrator for comprehensive processing
            orchestrator = ChunkingOrchestrator()

            # Create command
            command = ChunkingCommandFactory.create_document_upload_command(
                document_id, content, metadata.get('filename', 'unknown') if metadata else 'unknown'
            )

            # Execute chunking
            result = await orchestrator.execute_chunking(command)

            if result.success:
                # Store chunks using existing storage service
                storage_result = await self.storage_service.store_chunks(
                    document_id, result.chunks, metadata, tenant_id=tenant_id
                )
                
                return {
                    'success': True,
                    'document_id': document_id,
                    'chunk_count': len(result.chunks),
                    'plan': {
                        'strategy_type': result.strategy_used,
                        'fallback_chain': result.fallback_chain,
                        'reasoning': f'Selected {result.strategy_used} from chain {result.fallback_chain}'
                    },
                    'quality_assessment': result.quality_metrics,
                    'storage_result': storage_result,
                    'embedding_storage_result': result.embedding_results is not None,
                    'embedding_metrics': len(result.embedding_results) if result.embedding_results else 0,
                    'performance_metrics': result.performance_metrics,
                    'orchestrator_used': True
                }
            else:
                # Fallback to original method
                return await self._fallback_to_original_processing(document_id, content, metadata)
                
        except Exception as e:
            logger.error(f"Orchestrator processing failed: {e}")
            # Fallback to original method
            return await self._fallback_to_original_processing(document_id, content, metadata)
    
    def process_document_sync(self, content: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Synchronous version for backward compatibility."""
        # Simple strategy selection for sync version
        strategy = self.factory.create_strategy("sentence")
        chunks = strategy.chunk_text(content, metadata)
        
        # Convert chunks to expected format
        formatted_chunks = []
        for i, chunk in enumerate(chunks):
            formatted_chunks.append(type('Chunk', (), {
                'content': chunk['content'],
                'start_position': chunk.get('start_position', 0),
                'end_position': chunk.get('end_position', 0)
            })())
        
        quality_score = self._assess_quality_sync(formatted_chunks)
        
        return {
            "chunks": formatted_chunks,
            "strategy_used": "sentence",
            "quality_score": quality_score,
            "total_chunks": len(formatted_chunks)
        }
    
    def _create_intelligent_chunking_plan(self, content: str, doc_analysis: Dict[str, Any],
                                        content_metrics, metadata: Dict[str, Any] = None) -> ChunkingPlan:
        """Create intelligent chunking plan based on comprehensive document analysis."""
        
        # Get strategy recommendation from factory
        recommendation = self.factory.get_strategy_recommendation(content)
        
        return ChunkingPlan(
            strategy_type=recommendation['recommended_strategy'],
            chunk_size_range=(
                content_metrics.optimal_chunk_size // 2,
                content_metrics.optimal_chunk_size
            ),
            overlap_ratio=content_metrics.recommended_overlap,
            expected_chunk_count=max(1, len(content) // content_metrics.optimal_chunk_size),
            reasoning=recommendation['reason'],
            document_analysis=doc_analysis,
            content_metrics=recommendation['content_metrics']
        )
    
    async def _execute_chunking(self, content: str, plan: ChunkingPlan, 
                              metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Execute the chunking plan with selected strategy."""
        
        # Create strategy based on plan
        if plan.strategy_type == "auto":
            strategy = self.factory.auto_select_strategy(
                content,
                min_chunk_size=plan.chunk_size_range[0],
                max_chunk_size=plan.chunk_size_range[1]
            )
        else:
            strategy = self.factory.create_strategy(
                strategy_type=plan.strategy_type,
                min_chunk_size=plan.chunk_size_range[0],
                max_chunk_size=plan.chunk_size_range[1]
            )
        
        # Execute chunking
        chunks = strategy.chunk_text(content, metadata)
        
        # Add chunk indices and enhanced quality scores
        for i, chunk in enumerate(chunks):
            chunk['chunk_index'] = i
            chunk['quality_score'] = self._calculate_enhanced_chunk_quality_score(chunk, content, plan)
            
            # Add embedding readiness check
            chunk['embedding_ready'] = self.density_analyzer.analyze_chunk_quality(
                chunk['content'], plan.chunk_size_range[1]
            )['embedding_ready']
        
        return chunks
    
    def _assess_chunk_quality(self, chunks: List[Dict[str, Any]], content: str, 
                            plan: ChunkingPlan) -> Dict[str, Any]:
        """Enhanced quality assessment with multiple criteria."""
        
        if not chunks:
            return {
                'overall_quality': 0.0,
                'issues': ['No chunks generated'],
                'chunk_count_score': 0.0,
                'size_distribution_score': 0.0,
                'overlap_quality_score': 0.0,
                'boundary_quality_score': 0.0,
                'embedding_readiness_score': 0.0
            }
        
        # Assess chunk count vs expected
        actual_count = len(chunks)
        expected_count = plan.expected_chunk_count
        count_ratio = min(actual_count, expected_count) / max(actual_count, expected_count)
        chunk_count_score = count_ratio if abs(actual_count - expected_count) <= 3 else count_ratio * 0.8
        
        # Assess size distribution
        chunk_sizes = [chunk['size'] for chunk in chunks]
        avg_size = sum(chunk_sizes) / len(chunk_sizes)
        target_size = (plan.chunk_size_range[0] + plan.chunk_size_range[1]) / 2
        size_deviation = abs(avg_size - target_size) / target_size
        size_distribution_score = max(0.0, 1.0 - size_deviation)
        
        # Assess overlap quality
        overlap_chunks = [chunk for chunk in chunks if chunk.get('has_overlap', False)]
        if overlap_chunks and plan.overlap_ratio > 0:
            avg_overlap_size = sum(chunk.get('overlap_size', 0) for chunk in overlap_chunks) / len(overlap_chunks)
            expected_overlap_size = avg_size * plan.overlap_ratio
            if expected_overlap_size > 0:
                overlap_ratio = min(avg_overlap_size, expected_overlap_size) / max(avg_overlap_size, expected_overlap_size)
                overlap_quality_score = overlap_ratio
            else:
                overlap_quality_score = 1.0
        else:
            overlap_quality_score = 1.0 if plan.overlap_ratio == 0 else 0.5
        
        # Assess boundary quality (sentence completeness)
        complete_chunks = sum(1 for chunk in chunks if chunk['content'].rstrip().endswith(('.', '!', '?', ';')))
        boundary_quality_score = complete_chunks / len(chunks)
        
        # Assess embedding readiness
        embedding_ready_chunks = sum(1 for chunk in chunks if chunk.get('embedding_ready', False))
        embedding_readiness_score = embedding_ready_chunks / len(chunks)
        
        # Calculate overall quality with weighted scores
        overall_quality = (
            chunk_count_score * 0.2 +
            size_distribution_score * 0.25 +
            overlap_quality_score * 0.2 +
            boundary_quality_score * 0.2 +
            embedding_readiness_score * 0.15
        )
        
        return {
            'overall_quality': overall_quality,
            'chunk_count_score': chunk_count_score,
            'size_distribution_score': size_distribution_score,
            'overlap_quality_score': overlap_quality_score,
            'boundary_quality_score': boundary_quality_score,
            'embedding_readiness_score': embedding_readiness_score,
            'chunk_count': actual_count,
            'avg_chunk_size': avg_size,
            'target_chunk_size': target_size
        }
    
    def _calculate_enhanced_chunk_quality_score(self, chunk: Dict[str, Any], 
                                              full_content: str, plan: ChunkingPlan) -> float:
        """Calculate enhanced quality score for individual chunk."""
        content = chunk['content']
        
        # Use content density analyzer for comprehensive quality assessment
        quality_analysis = self.density_analyzer.analyze_chunk_quality(
            content, plan.chunk_size_range[1]
        )
        
        return quality_analysis['overall_quality']
    
    async def _retry_with_alternative_strategy(self, document_id: str, content: str, 
                                             original_plan: ChunkingPlan, 
                                             quality_assessment: Dict[str, Any],
                                             doc_analysis: Dict[str, Any],
                                             metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Retry chunking with alternative strategy if quality is poor."""
        
        # Create alternative plan with sentence strategy as fallback
        alternative_plan = ChunkingPlan(
            strategy_type="sentence",
            chunk_size_range=(400, 800),
            overlap_ratio=0.25,
            expected_chunk_count=len(content) // 600,
            reasoning="Fallback to sentence strategy due to quality issues",
            document_analysis=doc_analysis,
            content_metrics=original_plan.content_metrics
        )
        
        # Execute alternative chunking
        chunks = await self._execute_chunking(content, alternative_plan, metadata)
        
        # Assess new quality
        new_quality_assessment = self._assess_chunk_quality(chunks, content, alternative_plan)
        
        # Generate embeddings for alternative chunks
        chunk_embeddings = await self.chunk_embedding_service.generate_chunk_embeddings(
            chunks, document_id
        )
        
        # Store chunks regardless (with quality info)
        storage_result = await self.storage_service.store_chunks(
            document_id, chunks, metadata
        )
        
        # Store chunk embeddings
        embedding_storage_result = await self.chunk_embedding_service.store_chunk_embeddings(
            chunk_embeddings
        )
        
        return {
            'success': True,
            'document_id': document_id,
            'chunk_count': len(chunks),
            'original_plan': original_plan,
            'alternative_plan': alternative_plan,
            'original_quality': quality_assessment,
            'final_quality': new_quality_assessment,
            'storage_result': storage_result,
            'embedding_storage_result': embedding_storage_result,
            'embedding_metrics': self.embedding_metrics.get_metrics(),
            'used_fallback': True
        }
    
    async def _fallback_to_original_processing(self, document_id: str, content: str, 
                                             metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Fallback to original processing method."""
        # Step 1: Analyze document structure and content
        doc_analysis = analyze_document(content)
        content_metrics = self.density_analyzer.analyze_content_density(content, doc_analysis)
        
        # Step 2: Create intelligent chunking plan
        plan = self._create_intelligent_chunking_plan(content, doc_analysis, content_metrics, metadata)
        
        # Step 3: Execute chunking with selected strategy
        chunks = await self._execute_chunking(content, plan, metadata)
        
        # Step 4: Self-reflection and quality assessment
        quality_assessment = self._assess_chunk_quality(chunks, content, plan)
        
        # Step 5: Generate chunk-level embeddings
        chunk_embeddings = await self.chunk_embedding_service.generate_chunk_embeddings(
            chunks, document_id
        )
        
        # Step 6: Store chunks and embeddings if quality is acceptable
        if quality_assessment['overall_quality'] >= 0.6:
            storage_result = await self.storage_service.store_chunks(
                document_id, chunks, metadata
            )
            
            # Store chunk embeddings
            embedding_storage_result = await self.chunk_embedding_service.store_chunk_embeddings(
                chunk_embeddings
            )
            
            return {
                'success': True,
                'document_id': document_id,
                'chunk_count': len(chunks),
                'plan': plan,
                'quality_assessment': quality_assessment,
                'storage_result': storage_result,
                'embedding_storage_result': embedding_storage_result,
                'embedding_metrics': self.embedding_metrics.get_metrics(),
                'document_analysis': doc_analysis,
                'content_metrics': content_metrics.__dict__,
                'fallback_method': True
            }
        else:
            # Quality too low, try alternative strategy
            return await self._retry_with_alternative_strategy(
                document_id, content, plan, quality_assessment, doc_analysis, metadata
            )
    
    def _assess_quality_sync(self, chunks: List) -> float:
        """Backward compatibility method for sync quality assessment."""
        if not chunks:
            return 0.0
        
        # Simple quality metrics
        avg_size = sum(len(c.content) for c in chunks) / len(chunks)
        size_consistency = 1.0 - abs(avg_size - 1000) / 1000  # Target ~1000 chars
        
        # Check sentence completeness
        complete_sentences = sum(1 for c in chunks 
                               if c.content.strip().endswith(('.', '!', '?')))
        sentence_quality = complete_sentences / len(chunks)
        
        return (size_consistency + sentence_quality) / 2