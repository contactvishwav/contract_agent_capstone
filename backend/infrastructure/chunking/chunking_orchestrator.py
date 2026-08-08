"""Chunking orchestrator using Command pattern."""

import asyncio
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from .strategy_selector import select_best_strategy
from .document_analyzer import analyze_document
from .content_density_analyzer import ContentDensityAnalyzer
from .legal_chunking_facade import LegalChunkingFacade, LegalChunkingEnhancer
from .overlap_analyzer import OverlapAnalyzer
from .quality_validator import QualityValidator, QualityMetricsCollector
from .performance_optimizer import get_performance_optimizer, PerformanceMonitor
from .factory import ChunkingFactory


@dataclass
class ChunkingCommand:
    """Command for chunking operations."""
    document_id: str
    content: str
    metadata: Dict[str, Any]
    options: Dict[str, Any]


@dataclass
class ChunkingResult:
    """Result of chunking operation."""
    success: bool
    document_id: str
    chunks: List[Dict[str, Any]]
    strategy_used: str
    fallback_chain: List[str]
    quality_metrics: Dict[str, Any]
    performance_metrics: Dict[str, Any]
    embedding_results: Optional[List[Dict[str, Any]]] = None
    error_message: Optional[str] = None


class ChunkingOrchestrator:
    """Orchestrates the complete chunking pipeline using Command pattern."""
    
    def __init__(self):
        # Core components
        self.factory = ChunkingFactory()
        self.density_analyzer = ContentDensityAnalyzer()
        self.legal_facade = LegalChunkingFacade()
        self.legal_enhancer = LegalChunkingEnhancer(self.legal_facade)
        self.overlap_analyzer = OverlapAnalyzer()
        self.quality_validator = QualityValidator()
        self.performance_optimizer = get_performance_optimizer()
        self.performance_monitor = PerformanceMonitor()

        # Quality observer
        self.quality_collector = QualityMetricsCollector()
        self.quality_validator.add_observer(self.quality_collector)
    
    async def execute_chunking(self, command: ChunkingCommand) -> ChunkingResult:
        """Execute complete chunking pipeline."""
        start_time = time.time()
        
        try:
            # Step 1: Document Analysis with Caching
            doc_analysis = await self._analyze_document_with_cache(command.content)
            
            # Step 2: Content Density Analysis
            content_metrics = self.density_analyzer.analyze_content_density(
                command.content, doc_analysis
            )
            
            # Step 3: Strategy Selection with Fallback Chain
            best_strategy, fallback_chain, strategy_scores = select_best_strategy(
                command.content, doc_analysis, content_metrics
            )
            
            # Step 4: Legal Context Preparation
            legal_context = self.legal_facade.prepare_legal_context(command.content, doc_analysis)
            
            # Step 5: Execute Chunking with Fallback
            chunks, strategy_used = await self._execute_chunking_with_fallback(
                command, best_strategy, fallback_chain, legal_context, content_metrics
            )
            
            # Step 6: Advanced Overlap Analysis
            chunks = self.overlap_analyzer.analyze_and_apply_overlap(chunks, legal_context)
            
            # Step 7: Performance Optimization
            optimization_result = await self.performance_optimizer.optimize_chunking_pipeline(
                command.content, chunks, legal_context
            )
            chunks = optimization_result['optimized_chunks']

            # Step 8: Quality Validation
            quality_metrics = await self.quality_validator.validate_chunks(chunks, legal_context)

            # Step 10: Performance Monitoring
            processing_time = time.time() - start_time
            self.performance_monitor.record_processing_time(processing_time, len(chunks))
            self.performance_monitor.record_optimization_stats(optimization_result)
            
            return ChunkingResult(
                success=True,
                document_id=command.document_id,
                chunks=chunks,
                strategy_used=strategy_used,
                fallback_chain=fallback_chain,
                quality_metrics=quality_metrics,
                performance_metrics={
                    'processing_time': processing_time,
                    'optimization_stats': optimization_result,
                    'strategy_scores': {name: score.score for name, score in strategy_scores.items()}
                }
            )
            
        except Exception as e:
            return ChunkingResult(
                success=False,
                document_id=command.document_id,
                chunks=[],
                strategy_used='none',
                fallback_chain=[],
                quality_metrics={},
                performance_metrics={},
                error_message=str(e)
            )
    
    async def _analyze_document_with_cache(self, content: str) -> Dict[str, Any]:
        """Analyze document with caching optimization."""
        doc_hash = self.performance_optimizer._calculate_text_hash(content)
        
        # Check cache first
        cached_analysis = self.performance_optimizer.analysis_cache.get_cached_analysis(doc_hash)
        if cached_analysis:
            return cached_analysis
        
        # Perform analysis
        doc_analysis = analyze_document(content)
        
        # Cache result
        self.performance_optimizer.analysis_cache.cache_analysis(doc_hash, doc_analysis)
        
        return doc_analysis
    
    async def _execute_chunking_with_fallback(self, command: ChunkingCommand, 
                                            best_strategy: str, fallback_chain: List[str],
                                            legal_context: Dict[str, Any], 
                                            content_metrics) -> tuple[List[Dict[str, Any]], str]:
        """Execute chunking with fallback chain."""
        
        for strategy_name in fallback_chain:
            try:
                # Create strategy
                strategy = self.factory.create_strategy(
                    strategy_name,
                    min_chunk_size=content_metrics.optimal_chunk_size // 2,
                    max_chunk_size=content_metrics.optimal_chunk_size
                )
                
                # Enhance strategy with legal intelligence
                self._enhance_strategy_with_legal_context(strategy, strategy_name, 
                                                        command.content, legal_context)
                
                # Execute chunking
                chunks = strategy.chunk_text(command.content, command.metadata)
                
                # Validate minimum quality
                if await self._validate_minimum_quality(chunks):
                    return chunks, strategy_name
                
            except Exception as e:
                # Log error and try next strategy
                print(f"Strategy {strategy_name} failed: {e}")
                continue
        
        # If all strategies fail, use basic sentence chunking
        basic_strategy = self.factory.create_strategy('sentence')
        chunks = basic_strategy.chunk_text(command.content, command.metadata)
        return chunks, 'sentence_fallback'
    
    def _enhance_strategy_with_legal_context(self, strategy, strategy_name: str, 
                                           text: str, legal_context: Dict[str, Any]):
        """Enhance strategy with legal intelligence."""
        if strategy_name == 'section':
            self.legal_enhancer.enhance_section_strategy(strategy, text, legal_context)
        elif strategy_name == 'clause':
            self.legal_enhancer.enhance_clause_strategy(strategy, text, legal_context)
        elif strategy_name == 'paragraph':
            self.legal_enhancer.enhance_paragraph_strategy(strategy, text, legal_context)
    
    async def _validate_minimum_quality(self, chunks: List[Dict[str, Any]]) -> bool:
        """Validate minimum chunk quality."""
        if not chunks:
            return False
        
        # Basic quality checks
        avg_size = sum(chunk.get('size', len(chunk.get('content', ''))) for chunk in chunks) / len(chunks)
        
        # Minimum quality thresholds
        if avg_size < 50:  # Too small
            return False
        
        if len(chunks) > len(' '.join(chunk.get('content', '') for chunk in chunks)) / 100:  # Too many tiny chunks
            return False
        
        return True
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Get comprehensive performance report."""
        return {
            'performance_monitor': self.performance_monitor.get_performance_report(),
            'cache_stats': self.performance_optimizer.analysis_cache.get_cache_stats(),
            'quality_metrics': self.quality_collector.get_metrics()
        }


# Command factory for different chunking scenarios
class ChunkingCommandFactory:
    """Factory for creating chunking commands."""
    
    @staticmethod
    def create_document_upload_command(document_id: str, content: str, 
                                     filename: str) -> ChunkingCommand:
        """Create command for document upload scenario."""
        return ChunkingCommand(
            document_id=document_id,
            content=content,
            metadata={
                'filename': filename,
                'source': 'upload',
                'timestamp': time.time()
            },
            options={
                'generate_embeddings': True,
                'enable_quality_validation': True,
                'use_performance_optimization': True
            }
        )
    
    @staticmethod
    def create_batch_processing_command(document_id: str, content: str) -> ChunkingCommand:
        """Create command for batch processing scenario."""
        return ChunkingCommand(
            document_id=document_id,
            content=content,
            metadata={
                'source': 'batch',
                'timestamp': time.time()
            },
            options={
                'generate_embeddings': True,
                'enable_quality_validation': False,  # Skip for performance
                'use_performance_optimization': True
            }
        )
    
    @staticmethod
    def create_analysis_only_command(document_id: str, content: str) -> ChunkingCommand:
        """Create command for analysis-only scenario."""
        return ChunkingCommand(
            document_id=document_id,
            content=content,
            metadata={
                'source': 'analysis',
                'timestamp': time.time()
            },
            options={
                'generate_embeddings': False,
                'enable_quality_validation': True,
                'use_performance_optimization': False
            }
        )