"""
Test Pattern Integration - ReACT, Chain-of-Thought, Advanced RAG, and the
Pattern Orchestrator combining all three.

Absorbs test_ai_patterns.py (punch-list item 22): that file was a zero-
assert, unmocked async smoke script (would make real, live LLM/Neo4j
calls if ever made collectible) exercising the same four things this file
covers. Its ReACT/Chain-of-Thought coverage was already fully duplicated
below - TestAdvancedRAGAgent and TestPatternOrchestrator are the two
genuinely new additions, covering what test_ai_patterns.py exercised but
nothing else did.

Regression fix (live-infrastructure audit): this file previously did none
of the Neo4jGraph patching every other file in this suite does, and
TestReACTAgent made a real, unmocked ChatGoogleGenerativeAI().invoke() call
whenever GOOGLE_API_KEY was set (failures were swallowed, so the test
"passed" either way - but the call was genuinely made). It only worked at
all because pytest's alphabetical collection order let an earlier test
file's Neo4jGraph mock stay cached in sys.modules by the time this file's
tests ran - run this file alone and it would attempt a real Neo4j
connection (and, with a real API key configured, a real Gemini call).

Fixed by: (1) wrapping every import - including the two that used to be
inline inside test methods - in the standard patch block, with
MockNeo4jGraph.return_value.query.return_value = [] configured explicitly
(a bare MagicMock's .query(...) isn't iterable, which would make
PolicyRepository.get_applicable_policies re-raise and TestChainOfThoughtAgent's
success=True assertions fail); (2) pre-importing
backend.shared.utils.contract_search_tool so any later lazy import
(ChainOfThoughtAgent._risk_assessment_chain's `from backend.infrastructure.
policy_repository import PolicyRepository`, AuditLogger's lazy
Neo4jContractRepository) reuses the already-mocked module instead of
triggering a fresh real construction; (3) injecting a fake LLM into
ReACTAgent.clause_tool so ClauseDetectorTool never falls back to a real
get_default_llm().
"""

import pytest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

with patch("langchain_neo4j.Neo4jGraph") as _MockNeo4jGraph, \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    _MockNeo4jGraph.return_value.query.return_value = []
    from backend.agents.patterns.react_agent import ReACTAgent
    from backend.agents.patterns.chain_of_thought_agent import ChainOfThoughtAgent
    from backend.agents.patterns.pattern_selector import PatternSelector, AnalysisComplexity
    from backend.agents.patterns.advanced_rag_agent import AdvancedRAGAgent
    from backend.agents.patterns import advanced_rag_agent
    from backend.agents.patterns.pattern_orchestrator import PatternOrchestratorFactory
    from backend.agents.supervisor.interfaces import AgentContext, AgentResult
    from backend.agents.intelligence_state import IntelligenceState
    from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator
    from backend.agents.intelligence_tools import ClauseDetectorTool
    from backend.agents.llm_extraction_service import _LLMExtractionResponse
    # Pre-import so any later, unprotected import that does
    # `from backend.shared.utils.contract_search_tool import graph, embedding`
    # (policy_repository.py, contract_repository.py, audit_logger.py's
    # lazily-constructed Neo4jContractRepository, etc.) reuses this
    # already-mocked module from sys.modules instead of triggering a fresh,
    # real Neo4jGraph() construction.
    import backend.shared.utils.contract_search_tool  # noqa: F401


def _fake_clause_detector_tool():
    """A ClauseDetectorTool whose underlying LLM is a fake - matches the
    with_structured_output(..., include_raw=True) contract established
    across this suite (e.g. test_stubbed_llm_parsers.py's make_fake_llm).
    Returns zero clauses; ReACTAgent's confidence calculation only inspects
    the observation string's wording ("no matches"/"irrelevant"/"relevant"),
    none of which appear in "Found N clauses", so an empty result is enough
    to drive the same confidence progression the original assertions expect."""
    class FakeLLM:
        def with_structured_output(self, schema, include_raw=True):
            return self

        def invoke(self, prompt):
            return {
                "raw": SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1}),
                "parsed": _LLMExtractionResponse(clauses=[]),
                "parsing_error": None,
            }

    return ClauseDetectorTool(llm=FakeLLM())


class TestReACTAgent:
    """Test ReACT pattern agent"""

    @pytest.mark.asyncio
    async def test_react_agent_basic_execution(self):
        """Test basic ReACT agent execution"""
        agent = ReACTAgent(max_iterations=2)
        agent.clause_tool = _fake_clause_detector_tool()

        result = await agent.execute({
            'contract_text': 'Sample contract with termination clause and liability terms...',
            'contract_id': 'test_001'
        })

        assert result['success'] == True
        assert 'steps' in result
        assert result['pattern'] == 'ReACT'
        assert len(result['steps']) <= 2
        assert 'final_confidence' in result

    @pytest.mark.asyncio
    async def test_react_agent_convergence(self):
        """Test ReACT agent converges with high confidence"""
        agent = ReACTAgent(max_iterations=5)
        agent.clause_tool = _fake_clause_detector_tool()

        result = await agent.execute({
            'contract_text': 'This contract contains payment terms, liability clauses, and termination provisions.',
            'contract_id': 'test_002'
        })

        assert result['success'] == True
        assert result['final_confidence'] > 0.0

    @pytest.mark.asyncio
    async def test_react_agent_error_handling(self):
        """Test ReACT agent handles missing data"""
        agent = ReACTAgent()
        agent.clause_tool = _fake_clause_detector_tool()

        result = await agent.execute({
            'contract_id': 'test_003'
        })

        assert result['success'] == False
        assert 'error' in result


class TestChainOfThoughtAgent:
    """Test Chain-of-Thought pattern agent"""
    
    @pytest.mark.asyncio
    async def test_cot_agent_risk_assessment(self):
        """Test CoT agent risk assessment"""
        agent = ChainOfThoughtAgent()
        
        result = await agent.execute({
            'clauses': [
                {'clause_type': 'Payment Terms', 'content': 'Payment due in 60 days'},
                {'clause_type': 'Liability', 'content': 'Unlimited liability'}
            ],
            'task_type': 'risk_assessment',
            'contract_id': 'test_004'
        })
        
        assert result['success'] == True
        assert 'thought_chain' in result
        assert result['pattern'] == 'Chain-of-Thought'
        assert 'final_result' in result
        assert len(result['thought_chain']) > 0
    
    @pytest.mark.asyncio
    async def test_cot_agent_clause_analysis(self):
        """Test CoT agent clause analysis"""
        agent = ChainOfThoughtAgent()
        
        result = await agent.execute({
            'contract_text': 'Sample contract with various clauses...',
            'target_clause': 'termination',
            'task_type': 'clause_analysis',
            'contract_id': 'test_005'
        })
        
        assert result['success'] == True
        assert 'thought_chain' in result


class TestPatternSelector:
    """Test pattern selector logic"""
    
    def test_selector_complex_contract(self):
        """Test selector chooses ReACT for complex contracts"""
        pattern = PatternSelector.select_pattern({
            'contract_text': 'x' * 60000,  # Large contract
            'clauses': [{'type': 'test'}] * 25,
            'violations': []
        })
        
        assert pattern == 'react'
    
    def test_selector_moderate_contract(self):
        """Test selector chooses CoT for moderate contracts"""
        pattern = PatternSelector.select_pattern({
            'contract_text': 'x' * 15000,
            'clauses': [{'type': 'test'}] * 12,
            'violations': []
        })
        
        assert pattern == 'chain_of_thought'
    
    def test_selector_simple_contract(self):
        """Test selector uses standard for simple contracts"""
        pattern = PatternSelector.select_pattern({
            'contract_text': 'x' * 5000,
            'clauses': [{'type': 'test'}] * 5,
            'violations': []
        })
        
        assert pattern == 'standard'
    
    def test_complexity_assessment(self):
        """Test complexity assessment logic"""
        # Complex
        complexity = PatternSelector._assess_complexity({
            'contract_text': 'x' * 60000,
            'clauses': [],
            'violations': []
        })
        assert complexity == AnalysisComplexity.COMPLEX
        
        # Moderate
        complexity = PatternSelector._assess_complexity({
            'contract_text': 'x' * 15000,
            'clauses': [],
            'violations': []
        })
        assert complexity == AnalysisComplexity.MODERATE
        
        # Simple
        complexity = PatternSelector._assess_complexity({
            'contract_text': 'x' * 5000,
            'clauses': [],
            'violations': []
        })
        assert complexity == AnalysisComplexity.SIMPLE


class TestOrchestratorIntegration:
    """Test pattern integration with orchestrator"""
    
    def test_intelligence_state_has_pattern_fields(self):
        """Test IntelligenceState includes pattern fields"""
        # Verify pattern fields exist in type hints
        annotations = IntelligenceState.__annotations__
        assert 'pattern_used' in annotations
        assert 'pattern_analysis' in annotations

    @pytest.mark.asyncio
    async def test_pattern_analysis_workflow_node(self):
        """Test pattern_analysis node exists in workflow"""
        orchestrator = IntelligenceOrchestrator(llm=None)
        
        # Verify _pattern_analysis method exists
        assert hasattr(orchestrator, '_pattern_analysis')
        assert callable(orchestrator._pattern_analysis)


class TestLoggingIntegration:
    """Test centralized logging integration"""
    
    @pytest.mark.asyncio
    async def test_agents_use_audit_logger(self):
        """Test agents use AuditLogger"""
        agent = ReACTAgent()
        
        # Verify audit_logger exists
        assert hasattr(agent, 'audit_logger')
        assert agent.audit_logger is not None
    
    @pytest.mark.asyncio
    async def test_agents_use_workflow_tracker(self):
        """Test agents use workflow tracker"""
        agent = ChainOfThoughtAgent()
        
        result = await agent.execute({
            'clauses': [],
            'contract_id': 'test_logging'
        })
        
        # Execution should be tracked
        assert agent.execution is not None


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
    """Test Advanced RAG pattern agent. Unlike ReACT/CoT, this pattern
    makes no LLM calls at all - it's pure Neo4j retrieval plus
    deterministic Python analysis, so a fake graph is the only mocking
    needed."""

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


class TestPatternOrchestrator:
    """Test Pattern Orchestrator's synthesis logic, with its three sub-
    agents replaced by fakes - each pattern's own real behavior is already
    covered individually above (TestReACTAgent, TestChainOfThoughtAgent,
    TestAdvancedRAGAgent), so this isolates the orchestrator's own
    pattern-selection and results-synthesis logic."""

    def _orchestrator_with_fake_agents(self, react_result=None, cot_result=None, rag_result=None):
        orchestrator = PatternOrchestratorFactory.create_orchestrator()
        orchestrator.react_agent = SimpleNamespace(
            process=AsyncMock(return_value=react_result or {'success': False}))
        orchestrator.cot_agent = SimpleNamespace(
            process=AsyncMock(return_value=cot_result or {'success': False}))
        orchestrator.rag_agent = SimpleNamespace(
            process=AsyncMock(return_value=rag_result or {'success': False}))
        return orchestrator

    @pytest.mark.asyncio
    async def test_process_only_executes_requested_patterns(self):
        orchestrator = self._orchestrator_with_fake_agents(
            react_result={'success': True, 'final_confidence': 0.6},
            cot_result={'success': True, 'thought_chain': [],
                        'final_result': {'confidence': 0.8, 'violations': [], 'recommendations': []}},
        )

        result = await orchestrator.process({'patterns': ['react', 'cot'], 'query': 'q'})

        assert result['success'] is True
        assert set(result['individual_results'].keys()) == {'react', 'cot'}
        orchestrator.rag_agent.process.assert_not_called()

    @pytest.mark.asyncio
    async def test_synthesize_results_averages_confidence_across_patterns(self):
        orchestrator = self._orchestrator_with_fake_agents(
            react_result={'success': True, 'final_confidence': 0.6},
            cot_result={'success': True, 'thought_chain': [],
                        'final_result': {'confidence': 0.8, 'violations': [], 'recommendations': []}},
        )

        result = await orchestrator.process({'patterns': ['react', 'cot'], 'query': 'q'})

        assert result['synthesized_result']['overall_confidence'] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_key_findings_aggregate_from_react_and_cot(self):
        orchestrator = self._orchestrator_with_fake_agents(
            react_result={'success': True, 'final_confidence': 0.5,
                          'findings': [{'type': 'clause', 'content': 'termination clause', 'relevance': 0.9}]},
            cot_result={'success': True, 'thought_chain': [],
                        'final_result': {'confidence': 0.7, 'recommendations': [],
                                         'violations': [{'violation': 'unlimited liability',
                                                          'severity': 'HIGH', 'clause_type': 'liability'}]}},
        )

        result = await orchestrator.process({'patterns': ['react', 'cot'], 'query': 'q'})

        sources = {f['source'] for f in result['synthesized_result']['key_findings']}
        assert sources == {'react', 'cot'}

    def test_execute_implements_iagent_protocol(self):
        # execute() drives its own asyncio.run(...) internally, so this
        # must be a plain (non-async-def) test - calling it from inside an
        # already-running pytest-asyncio event loop would raise.
        orchestrator = self._orchestrator_with_fake_agents(
            react_result={'success': True, 'final_confidence': 0.9},
        )
        context = AgentContext(input_data={'patterns': ['react'], 'query': 'q'}, workflow_context=None)

        result = orchestrator.execute(context)

        assert isinstance(result, AgentResult)
        assert result.status == 'success'

    def test_create_for_task_clause_extraction_increases_react_iterations(self):
        orchestrator = PatternOrchestratorFactory.create_for_task('clause_extraction')

        assert orchestrator.react_agent.max_iterations == 5


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
