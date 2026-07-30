"""Test AI Patterns implementation."""

import asyncio


async def test_react_pattern():
    """Test ReACT pattern for iterative clause extraction."""
    from backend.agents.patterns.react_agent import ReACTAgent
    
    print("\n" + "="*60)
    print("Testing ReACT Pattern - Iterative Clause Extraction")
    print("="*60)
    
    agent = ReACTAgent(max_iterations=3)
    
    contract_text = """
    TERMINATION CLAUSE
    
    Either party may terminate this agreement with 30 days written notice.
    Immediate termination is allowed in case of material breach.
    Upon termination, all outstanding payments become due immediately.
    
    LIABILITY CLAUSE
    
    The Company's total liability shall not exceed the contract value.
    Unlimited liability applies for gross negligence or willful misconduct.
    """
    
    context = {
        'query': 'termination rights',
        'contract_text': contract_text
    }
    
    result = await agent.process(context)
    
    if result.get('success'):
        print(f"\n✓ ReACT Analysis Complete")
        print(f"  Final Confidence: {result['final_confidence']:.2f}")
        print(f"  Iterations: {len(result['steps'])}")
        print(f"  Findings: {len(result['findings'])}")
        
        print("\n  Reasoning Steps:")
        for step in result['steps']:
            print(f"\n  Iteration {step['iteration']}:")
            print(f"    Reasoning: {step['reasoning']}")
            print(f"    Action: {step['action']}")
            print(f"    Observation: {step['observation']}")
            print(f"    Confidence: {step['confidence']:.2f}")
    else:
        print(f"\n✗ ReACT Analysis Failed: {result.get('error')}")


async def test_chain_of_thought_pattern():
    """Test Chain-of-Thought pattern for risk assessment."""
    from backend.agents.patterns.chain_of_thought_agent import ChainOfThoughtAgent
    
    print("\n" + "="*60)
    print("Testing Chain-of-Thought Pattern - Risk Assessment")
    print("="*60)
    
    agent = ChainOfThoughtAgent()
    
    clauses = [
        {'type': 'liability', 'content': 'The Company shall have unlimited liability for all damages.'},
        {'type': 'termination', 'content': 'Either party may terminate immediately without notice.'}
    ]
    
    policies = {
        'liability': {'type': 'liability', 'max_amount': 1000000},
        'termination': {'type': 'termination', 'min_notice_days': 30}
    }
    
    context = {
        'task_type': 'risk_assessment',
        'clauses': clauses,
        'policies': policies
    }
    
    result = await agent.process(context)
    
    if result.get('success'):
        print(f"\n✓ Chain-of-Thought Analysis Complete")
        
        final_result = result['final_result']
        print(f"  Risk Score: {final_result['risk_score']}/10")
        print(f"  Violations: {len(final_result['violations'])}")
        print(f"  Recommendations: {len(final_result['recommendations'])}")
        print(f"  Confidence: {final_result['confidence']:.2f}")
        
        print("\n  Thought Chain:")
        for step in result['thought_chain']:
            print(f"\n  Step {step['step_number']}: {step['description']}")
            print(f"    Reasoning: {step['reasoning']}")
            print(f"    Confidence: {step['confidence']:.2f}")
        
        if final_result['violations']:
            print("\n  Violations Found:")
            for violation in final_result['violations']:
                print(f"    - {violation['clause_type']}: {violation['violation']} ({violation['severity']})")
    else:
        print(f"\n✗ Chain-of-Thought Analysis Failed: {result.get('error')}")


async def test_advanced_rag_pattern():
    """Test Advanced RAG pattern for contextual analysis."""
    from backend.agents.patterns.advanced_rag_agent import AdvancedRAGAgent
    
    print("\n" + "="*60)
    print("Testing Advanced RAG Pattern - Contextual Analysis")
    print("="*60)
    
    agent = AdvancedRAGAgent()
    
    context = {
        'query': 'liability terms',
        'contract_id': 'TEST_001'
    }
    
    result = await agent.process(context)
    
    if result.get('success'):
        print(f"\n✓ Advanced RAG Analysis Complete")
        
        rag_context = result['rag_context']
        print(f"  Context Score: {rag_context['context_score']:.2f}")
        print(f"  Similar Contracts: {rag_context['similar_contracts_count']}")
        print(f"  Precedents: {rag_context['precedents_count']}")
        print(f"  Company History: {rag_context['company_history_count']}")
        
        analysis = result['analysis']
        print(f"\n  Insights: {len(analysis['insights'])}")
        print(f"  Recommendations: {len(analysis['recommendations'])}")
        print(f"  Comparisons: {len(analysis['comparisons'])}")
        
        if analysis['insights']:
            print("\n  Key Insights:")
            for insight in analysis['insights'][:3]:
                print(f"    - {insight['insight']} (confidence: {insight['confidence']:.2f})")
    else:
        print(f"\n✗ Advanced RAG Analysis Failed: {result.get('error')}")


async def test_pattern_orchestrator():
    """Test Pattern Orchestrator combining all patterns."""
    from backend.agents.patterns.pattern_orchestrator import PatternOrchestratorFactory
    
    print("\n" + "="*60)
    print("Testing Pattern Orchestrator - Combined Analysis")
    print("="*60)
    
    orchestrator = PatternOrchestratorFactory.create_for_task('risk_assessment')
    
    contract_text = """
    LIABILITY CLAUSE
    The Company shall have unlimited liability for all damages and losses.
    
    TERMINATION CLAUSE
    Either party may terminate immediately without notice.
    """
    
    clauses = [
        {'type': 'liability', 'content': 'The Company shall have unlimited liability for all damages.'},
        {'type': 'termination', 'content': 'Either party may terminate immediately without notice.'}
    ]
    
    policies = {
        'liability': {'type': 'liability', 'max_amount': 1000000},
        'termination': {'type': 'termination', 'min_notice_days': 30}
    }
    
    context = {
        'task_type': 'risk_assessment',
        'patterns': ['react', 'cot', 'rag'],
        'query': 'liability and termination risks',
        'contract_text': contract_text,
        'contract_id': 'TEST_002',
        'clauses': clauses,
        'policies': policies
    }
    
    result = await orchestrator.process(context)
    
    if result.get('success'):
        print(f"\n✓ Pattern Orchestration Complete")
        print(f"  Patterns Used: {', '.join(result['patterns_used'])}")
        
        synthesis = result['synthesized_result']
        print(f"\n  Overall Confidence: {synthesis['overall_confidence']:.2f}")
        print(f"  Key Findings: {len(synthesis['key_findings'])}")
        print(f"  Recommendations: {len(synthesis['recommendations'])}")
        print(f"  Reasoning Steps: {len(synthesis['reasoning_trace'])}")
        print(f"  Contextual Insights: {len(synthesis['contextual_insights'])}")
        
        print("\n  Confidence Scores by Pattern:")
        for pattern, score in synthesis['confidence_scores'].items():
            print(f"    {pattern.upper()}: {score:.2f}")
        
        if synthesis['key_findings']:
            print("\n  Key Findings:")
            for finding in synthesis['key_findings'][:3]:
                print(f"    - [{finding['source'].upper()}] {finding.get('content', finding.get('violation', 'N/A'))[:80]}...")
        
        if synthesis['recommendations']:
            print("\n  Recommendations:")
            for rec in synthesis['recommendations'][:3]:
                print(f"    - [{rec['priority']}] {rec['recommendation'][:80]}...")
    else:
        print(f"\n✗ Pattern Orchestration Failed: {result.get('error')}")


async def main():
    """Run all AI pattern tests."""
    print("\n" + "="*60)
    print("AI PATTERNS IMPLEMENTATION TEST")
    print("="*60)
    
    try:
        await test_react_pattern()
        await test_chain_of_thought_pattern()
        await test_advanced_rag_pattern()
        await test_pattern_orchestrator()
        
        print("\n" + "="*60)
        print("✓ ALL AI PATTERNS TESTS COMPLETED")
        print("="*60)
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())