"""Factory for creating chunking strategies with auto-selection."""

from typing import Dict, Any, Optional
from .base_strategy import IChunkingStrategy as ChunkingStrategy
from .sentence_strategy import SentenceAdaptiveStrategy
from .sentence_strategy import SentenceAdaptiveStrategy as SentenceStrategy  # Alias for compatibility
from .paragraph_strategy import ParagraphStrategy
from .section_strategy import SectionStrategy
from .clause_strategy import ClauseStrategy
from .hybrid_strategy import HybridStrategy
from .policy_strategy import PolicyChunkingStrategy
from .document_analyzer import analyze_document
from .content_density_analyzer import ContentDensityAnalyzer


class ChunkingFactory:
    """Factory for creating appropriate chunking strategies with intelligent selection."""
    
    @staticmethod
    def create_strategy(strategy_type: str = "auto", **kwargs) -> ChunkingStrategy:
        """Create a chunking strategy based on type."""
        
        strategies = {
            "sentence_adaptive": SentenceAdaptiveStrategy,
            "sentence": SentenceAdaptiveStrategy,  # Alias
            "paragraph": ParagraphStrategy,
            "section": SectionStrategy,
            "clause": ClauseStrategy,
            "hybrid": HybridStrategy,
            "policy": PolicyChunkingStrategy
        }
        
        if strategy_type == "auto":
            # Auto-selection requires text analysis
            return HybridStrategy(**kwargs)  # Hybrid handles auto-selection internally
        elif strategy_type in strategies:
            return strategies[strategy_type](**kwargs)
        else:
            # Default to sentence strategy
            return SentenceAdaptiveStrategy(**kwargs)
    
    @staticmethod
    def auto_select_strategy(text: str, **kwargs) -> ChunkingStrategy:
        """Automatically select the best chunking strategy using intelligent selection."""
        from .strategy_selector import select_best_strategy
        from .document_analyzer import analyze_document
        from .content_density_analyzer import ContentDensityAnalyzer
        
        # Use intelligent strategy selection
        doc_analysis = analyze_document(text)
        density_analyzer = ContentDensityAnalyzer()
        content_metrics = density_analyzer.analyze_content_density(text, doc_analysis)
        
        # Get best strategy with fallback chain
        best_strategy, fallback_chain, strategy_scores = select_best_strategy(
            text, doc_analysis, content_metrics
        )
        
        # Create the selected strategy
        return ChunkingFactory.create_strategy(
            best_strategy,
            min_chunk_size=kwargs.get('min_chunk_size', content_metrics.optimal_chunk_size // 2),
            max_chunk_size=kwargs.get('max_chunk_size', content_metrics.optimal_chunk_size)
        )
    
    @staticmethod
    def get_strategy_recommendation(text: str) -> Dict[str, Any]:
        """Get strategy recommendation with reasoning."""
        doc_analysis = analyze_document(text)
        density_analyzer = ContentDensityAnalyzer()
        content_metrics = density_analyzer.analyze_content_density(text, doc_analysis)
        
        # Determine recommended strategy
        if doc_analysis.get('has_hierarchical_structure', False):
            recommended = "section"
            reason = f"Document has {doc_analysis.get('section_count', 0)} sections with hierarchical structure"
        elif doc_analysis.get('is_legal_document', False) and content_metrics.clause_density > 2.0:
            recommended = "clause"
            reason = f"Legal document with high clause density ({content_metrics.clause_density:.1f}%)"
        elif doc_analysis.get('paragraph_count', 0) >= 5:
            recommended = "paragraph"
            reason = f"Well-structured document with {doc_analysis.get('paragraph_count', 0)} paragraphs"
        elif content_metrics.complexity_score > 0.7:
            recommended = "hybrid"
            reason = f"Complex content (complexity: {content_metrics.complexity_score:.2f}) benefits from hybrid approach"
        else:
            recommended = "sentence"
            reason = "Standard sentence-based chunking suitable for this content"
        
        return {
            'recommended_strategy': recommended,
            'reason': reason,
            'optimal_chunk_size': content_metrics.optimal_chunk_size,
            'recommended_overlap': content_metrics.recommended_overlap,
            'document_analysis': doc_analysis,
            'content_metrics': {
                'complexity_score': content_metrics.complexity_score,
                'clause_density': content_metrics.clause_density,
                'legal_term_density': content_metrics.legal_term_density,
                'sentence_density': content_metrics.sentence_density
            }
        }
    
    @staticmethod
    def get_available_strategies() -> Dict[str, str]:
        """Get list of available chunking strategies."""
        return {
            "auto": "Automatically select best strategy based on document analysis",
            "sentence": "Sentence-based chunking with adaptive overlap",
            "paragraph": "Paragraph-boundary aware chunking",
            "section": "Legal section-aware chunking with header detection",
            "clause": "Legal clause-boundary detection chunking",
            "hybrid": "Combines multiple strategies for optimal results",
            "policy": "Legal-aware chunking for policy documents by rule and section",
        }