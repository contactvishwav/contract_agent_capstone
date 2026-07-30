"""Advanced RAG Pattern Agent - Sophisticated retrieval with precedent lookup."""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import logging

from backend.shared.utils.contract_search_tool import graph, embedding
from backend.shared.utils.vector_index_config import CONTRACT_EMBEDDING_INDEX, VECTOR_SEARCH_OVERFETCH

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


@dataclass
class RAGContext:
    query: str
    similar_contracts: List[Dict[str, Any]]
    precedents: List[Dict[str, Any]]
    company_history: List[Dict[str, Any]]
    context_score: float


class AdvancedRAGAgent:
    def __init__(self):
        self.context_cache = {}
    
    async def process(self, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            query = context.get('query', '')
            contract_id = context.get('contract_id', '')
            
            if not query:
                return {'error': 'Missing query'}
            
            # Build comprehensive context
            rag_context = await self._build_rag_context(query, contract_id)
            
            # Generate context-aware analysis
            analysis = await self._generate_contextual_analysis(rag_context, context)
            
            return {
                'success': True,
                'rag_context': self._context_to_dict(rag_context),
                'analysis': analysis
            }
            
        except Exception as e:
            logger.error(f"Advanced RAG error: {e}")
            return {'error': str(e)}
    
    async def _build_rag_context(self, query: str, contract_id: str) -> RAGContext:
        # Generate query embedding
        query_embedding = embedding.embed_query(query)
        
        # Find similar contracts
        similar_contracts = await self._find_similar_contracts(query_embedding, contract_id)
        
        # Find legal precedents
        precedents = await self._find_legal_precedents(query, similar_contracts)
        
        # Get company contract history
        company_history = await self._get_company_history(query, contract_id)
        
        # Calculate context quality score
        context_score = self._calculate_context_score(similar_contracts, precedents, company_history)
        
        return RAGContext(query, similar_contracts, precedents, company_history, context_score)
    
    async def _find_similar_contracts(self, query_embedding: List[float], 
                                    exclude_contract_id: str) -> List[Dict[str, Any]]:
        try:
            # Query the vector index for candidates (instead of scoring
            # every Contract node), then exclude the source contract itself.
            cypher_query = f"""
            CALL db.index.vector.queryNodes('{CONTRACT_EMBEDDING_INDEX}', $k, $query_embedding)
            YIELD node AS c, score AS similarity
            WHERE c.file_id <> $exclude_id AND similarity > 0.7
            RETURN c.file_id as contract_id, c.summary as summary,
                   c.contract_type as contract_type, c.effective_date as effective_date,
                   similarity
            ORDER BY similarity DESC
            LIMIT 5
            """

            result = graph.query(cypher_query, {
                'query_embedding': query_embedding,
                'exclude_id': exclude_contract_id,
                'k': VECTOR_SEARCH_OVERFETCH,
            })
            
            return [dict(record) for record in result]
            
        except Exception as e:
            logger.error(f"Error finding similar contracts: {e}")
            return []
    
    async def _find_legal_precedents(self, query: str, 
                                   similar_contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        precedents = []
        
        # Extract contract types from similar contracts
        contract_types = set(c.get('contract_type', '') for c in similar_contracts)
        
        # Find precedents based on contract types and query terms
        for contract_type in contract_types:
            if not contract_type:
                continue
                
            try:
                # Search for precedent patterns in existing contracts
                precedent_query = """
                MATCH (c:Contract)
                WHERE c.contract_type = $contract_type
                WITH c, c.summary as summary
                WHERE toLower(summary) CONTAINS toLower($query_term)
                RETURN c.file_id as contract_id, c.summary as summary,
                       c.contract_type as contract_type, c.effective_date as date,
                       'precedent' as type
                ORDER BY c.effective_date DESC
                LIMIT 3
                """
                
                result = graph.query(precedent_query, {
                    'contract_type': contract_type,
                    'query_term': query
                })
                
                precedents.extend([dict(record) for record in result])
                
            except Exception as e:
                logger.error(f"Error finding precedents for {contract_type}: {e}")
        
        return precedents[:5]  # Limit to top 5 precedents
    
    async def _get_company_history(self, query: str, current_contract_id: str) -> List[Dict[str, Any]]:
        try:
            # Get company's historical contract patterns
            history_query = """
            MATCH (c:Contract)
            WHERE c.file_id <> $current_id
            WITH c
            ORDER BY c.effective_date DESC
            RETURN c.file_id as contract_id, c.summary as summary,
                   c.contract_type as contract_type, c.effective_date as date,
                   c.total_amount as amount
            LIMIT 10
            """
            
            result = graph.query(history_query, {'current_id': current_contract_id})
            history = [dict(record) for record in result]
            
            # Filter history relevant to query
            relevant_history = []
            query_lower = query.lower()
            
            for contract in history:
                summary = contract.get('summary', '').lower()
                contract_type = contract.get('contract_type', '').lower()
                
                if (query_lower in summary or 
                    any(term in summary for term in query_lower.split()) or
                    query_lower in contract_type):
                    relevant_history.append(contract)
            
            return relevant_history[:5]  # Top 5 relevant historical contracts
            
        except Exception as e:
            logger.error(f"Error getting company history: {e}")
            return []
    
    def _calculate_context_score(self, similar_contracts: List[Dict[str, Any]], 
                               precedents: List[Dict[str, Any]], 
                               company_history: List[Dict[str, Any]]) -> float:
        # Calculate context quality based on available information
        score = 0.0
        
        # Similar contracts contribute 40%
        if similar_contracts:
            similarity_scores = [c.get('similarity', 0) for c in similar_contracts]
            avg_similarity = sum(similarity_scores) / len(similarity_scores)
            score += avg_similarity * 0.4
        
        # Precedents contribute 30%
        if precedents:
            score += min(len(precedents) / 5.0, 1.0) * 0.3
        
        # Company history contributes 30%
        if company_history:
            score += min(len(company_history) / 5.0, 1.0) * 0.3
        
        return min(score, 1.0)
    
    async def _generate_contextual_analysis(self, rag_context: RAGContext, 
                                          original_context: Dict[str, Any]) -> Dict[str, Any]:
        analysis = {
            'query': rag_context.query,
            'context_quality': rag_context.context_score,
            'insights': [],
            'recommendations': [],
            'comparisons': []
        }
        
        # Generate insights from similar contracts
        if rag_context.similar_contracts:
            similar_insights = self._analyze_similar_contracts(rag_context.similar_contracts)
            analysis['insights'].extend(similar_insights)
        
        # Generate insights from precedents
        if rag_context.precedents:
            precedent_insights = self._analyze_precedents(rag_context.precedents)
            analysis['insights'].extend(precedent_insights)
        
        # Generate insights from company history
        if rag_context.company_history:
            history_insights = self._analyze_company_history(rag_context.company_history)
            analysis['insights'].extend(history_insights)
        
        # Generate comparative analysis
        analysis['comparisons'] = self._generate_comparisons(rag_context)
        
        # Generate recommendations based on context
        analysis['recommendations'] = self._generate_contextual_recommendations(rag_context)
        
        return analysis
    
    def _analyze_similar_contracts(self, similar_contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        insights = []
        
        if not similar_contracts:
            return insights
        
        # Analyze contract types
        contract_types = [c.get('contract_type', '') for c in similar_contracts]
        most_common_type = max(set(contract_types), key=contract_types.count) if contract_types else 'Unknown'
        
        insights.append({
            'type': 'similar_contracts',
            'insight': f"Found {len(similar_contracts)} similar contracts, most commonly {most_common_type}",
            'confidence': 0.8,
            'data': {'most_common_type': most_common_type, 'count': len(similar_contracts)}
        })
        
        # Analyze similarity scores
        similarities = [c.get('similarity', 0) for c in similar_contracts]
        avg_similarity = sum(similarities) / len(similarities)
        
        insights.append({
            'type': 'similarity_analysis',
            'insight': f"Average similarity score: {avg_similarity:.2f} (High similarity indicates strong precedent)",
            'confidence': avg_similarity,
            'data': {'avg_similarity': avg_similarity, 'max_similarity': max(similarities)}
        })
        
        return insights
    
    def _analyze_precedents(self, precedents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        insights = []
        
        if not precedents:
            return insights
        
        # Analyze precedent patterns
        precedent_types = [p.get('contract_type', '') for p in precedents]
        unique_types = set(precedent_types)
        
        insights.append({
            'type': 'legal_precedents',
            'insight': f"Found {len(precedents)} legal precedents across {len(unique_types)} contract types",
            'confidence': 0.7,
            'data': {'precedent_count': len(precedents), 'contract_types': list(unique_types)}
        })
        
        return insights
    
    def _analyze_company_history(self, company_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        insights = []
        
        if not company_history:
            return insights
        
        # Analyze historical patterns
        historical_types = [h.get('contract_type', '') for h in company_history]
        amounts = [h.get('amount', 0) for h in company_history if h.get('amount')]
        
        insights.append({
            'type': 'company_history',
            'insight': f"Company has {len(company_history)} relevant historical contracts",
            'confidence': 0.8,
            'data': {
                'historical_count': len(company_history),
                'contract_types': list(set(historical_types)),
                'avg_amount': sum(amounts) / len(amounts) if amounts else 0
            }
        })
        
        return insights
    
    def _generate_comparisons(self, rag_context: RAGContext) -> List[Dict[str, Any]]:
        comparisons = []
        
        # Compare with similar contracts
        if rag_context.similar_contracts:
            for contract in rag_context.similar_contracts[:3]:  # Top 3
                comparisons.append({
                    'type': 'similar_contract',
                    'contract_id': contract.get('contract_id', ''),
                    'similarity': contract.get('similarity', 0),
                    'contract_type': contract.get('contract_type', ''),
                    'summary': contract.get('summary', '')[:100] + '...'
                })
        
        return comparisons
    
    def _generate_contextual_recommendations(self, rag_context: RAGContext) -> List[Dict[str, Any]]:
        recommendations = []
        
        # Recommendations based on similar contracts
        if rag_context.similar_contracts:
            avg_similarity = sum(c.get('similarity', 0) for c in rag_context.similar_contracts) / len(rag_context.similar_contracts)
            
            if avg_similarity > 0.8:
                recommendations.append({
                    'type': 'high_similarity',
                    'recommendation': "High similarity with existing contracts suggests standard terms. Consider leveraging proven clauses.",
                    'priority': 'MEDIUM',
                    'confidence': avg_similarity
                })
        
        # Recommendations based on precedents
        if rag_context.precedents:
            recommendations.append({
                'type': 'precedent_based',
                'recommendation': f"Review {len(rag_context.precedents)} legal precedents for best practices and risk mitigation.",
                'priority': 'HIGH',
                'confidence': 0.8
            })
        
        # Recommendations based on company history
        if rag_context.company_history:
            recommendations.append({
                'type': 'history_based',
                'recommendation': "Compare terms with company's historical contracts to ensure consistency with established practices.",
                'priority': 'MEDIUM',
                'confidence': 0.7
            })
        
        return recommendations
    
    def _context_to_dict(self, rag_context: RAGContext) -> Dict[str, Any]:
        return {
            'query': rag_context.query,
            'similar_contracts_count': len(rag_context.similar_contracts),
            'precedents_count': len(rag_context.precedents),
            'company_history_count': len(rag_context.company_history),
            'context_score': rag_context.context_score,
            'similar_contracts': rag_context.similar_contracts,
            'precedents': rag_context.precedents,
            'company_history': rag_context.company_history
        }