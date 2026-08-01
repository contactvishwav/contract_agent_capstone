"""
Test Advanced RAG pattern agent.

Formerly also tested ReACTAgent, ChainOfThoughtAgent, PatternSelector, and
PatternOrchestrator - all removed (backend/agents/patterns/__init__.py has
the full rationale; docs/CAPSTONE_SUMMARY.md has the removal decision,
same precedent as the Supervisor orchestration removal): confirmed
unreachable in real usage (use_planning defaults to True at every layer
including the frontend, which never overrides it), their output never
influenced any downstream field even when manually triggered
(pattern_used/pattern_analysis were a dead side-channel, never rendered
anywhere), and ChainOfThoughtAgent's policy check had drifted into a
third, independent hardcoded keyword matcher duplicating what
PolicyEvaluationService already does correctly elsewhere.

AdvancedRAGAgent was not part of that finding and stays - this file
narrows to just its coverage. Unlike ReACT/CoT, this pattern makes no LLM
calls at all - it's pure Neo4j retrieval plus deterministic Python
analysis, so a fake graph is the only mocking needed.
"""

import pytest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph") as _MockNeo4jGraph, \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    _MockNeo4jGraph.return_value.query.return_value = []
    from backend.agents.patterns.advanced_rag_agent import AdvancedRAGAgent
    from backend.agents.patterns import advanced_rag_agent


class _FakeAdvancedRAGGraph:
    """Minimal in-memory stand-in for the three real Cypher shapes
    AdvancedRAGAgent._build_rag_context issues - dispatches on a distinctive
    substring per query rather than modeling real Neo4j matching, matching
    the FakeGraph convention used across this suite."""

    def __init__(self, similar=None, precedents=None, history=None):
        self._similar = similar if similar is not None else []
        self._precedents = precedents if precedents is not None else []
        self._history = history if history is not None else []

    def query(self, cypher, params=None):
        if "queryNodes" in cypher:
            return self._similar
        if "'precedent' as type" in cypher:
            return self._precedents
        if "c.total_amount as amount" in cypher:
            return self._history
        return []


class TestAdvancedRAGAgent:
    """Test Advanced RAG pattern agent."""

    @pytest.mark.asyncio
    async def test_process_requires_a_query(self):
        agent = AdvancedRAGAgent()

        result = await agent.process({'contract_id': 'c1'})

        assert 'error' in result
        assert 'success' not in result

    @pytest.mark.asyncio
    async def test_process_returns_success_with_populated_context(self):
        agent = AdvancedRAGAgent()
        fake_graph = _FakeAdvancedRAGGraph(
            similar=[{'contract_id': 'c2', 'summary': 'A liability agreement',
                      'contract_type': 'MSA', 'effective_date': '2024-01-01', 'similarity': 0.9}],
            precedents=[{'contract_id': 'c3', 'summary': 'precedent summary',
                         'contract_type': 'MSA', 'date': '2023-01-01', 'type': 'precedent'}],
            history=[{'contract_id': 'c4', 'summary': 'liability terms history',
                      'contract_type': 'MSA', 'date': '2022-01-01', 'amount': 5000}],
        )

        with patch.object(advanced_rag_agent, 'graph', fake_graph):
            result = await agent.process({'query': 'liability terms', 'contract_id': 'c1'})

        assert result['success'] is True
        assert result['rag_context']['similar_contracts_count'] == 1
        assert result['rag_context']['precedents_count'] == 1
        assert result['rag_context']['company_history_count'] == 1
        assert result['rag_context']['context_score'] > 0.0
        assert len(result['analysis']['insights']) > 0

    @pytest.mark.asyncio
    async def test_context_score_is_zero_with_no_data_available(self):
        agent = AdvancedRAGAgent()

        with patch.object(advanced_rag_agent, 'graph', _FakeAdvancedRAGGraph()):
            result = await agent.process({'query': 'anything', 'contract_id': 'c1'})

        assert result['success'] is True
        assert result['rag_context']['context_score'] == 0.0
        assert result['analysis']['insights'] == []

    @pytest.mark.asyncio
    async def test_precedents_found_generate_a_precedent_review_recommendation(self):
        agent = AdvancedRAGAgent()
        fake_graph = _FakeAdvancedRAGGraph(
            similar=[{'contract_id': 'c2', 'summary': 's', 'contract_type': 'MSA', 'similarity': 0.5}],
            precedents=[{'contract_id': 'c3', 'summary': 's', 'contract_type': 'MSA', 'type': 'precedent'}],
        )

        with patch.object(advanced_rag_agent, 'graph', fake_graph):
            result = await agent.process({'query': 'liability', 'contract_id': 'c1'})

        recommendation_types = {r['type'] for r in result['analysis']['recommendations']}
        assert 'precedent_based' in recommendation_types


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
