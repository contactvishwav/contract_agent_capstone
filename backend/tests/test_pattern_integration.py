"""
Test Pattern Integration - ReACT and Chain-of-Thought
Tests pattern agents, selector, and orchestrator integration.

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
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph") as _MockNeo4jGraph, \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    _MockNeo4jGraph.return_value.query.return_value = []
    from backend.agents.patterns.react_agent import ReACTAgent
    from backend.agents.patterns.chain_of_thought_agent import ChainOfThoughtAgent
    from backend.agents.patterns.pattern_selector import PatternSelector, AnalysisComplexity
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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
