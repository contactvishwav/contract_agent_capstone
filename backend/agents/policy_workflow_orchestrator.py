"""Policy workflow orchestrator - executes policy agents via the shared
IAgent/AgentRegistry protocol (agents/supervisor/interfaces.py).

Previously also constructed a SupervisorAgent for circuit-breaker/retry/
quality-gate coordination, but process_policy_document below never called
it - it always ran its own manual step loop instead. Removed as dead
weight (see docs/CAPSTONE_SUMMARY.md's orchestration-consolidation
decision); the registry/agent protocol below is the part that was
actually load-bearing."""

from typing import Dict, Any, List
from backend.agents.supervisor.agent_registry import AgentRegistry
from backend.agents.supervisor.interfaces import AgentContext, WorkflowContext
from backend.agents.policy_agents import PolicyChunkingAgent, PolicyExtractionAgent, PolicyComplianceAgent


class PolicyWorkflowOrchestrator:
    """Orchestrates policy workflows by executing registered policy agents in sequence."""

    def __init__(self):
        self.registry = AgentRegistry()

        # Register policy agents with existing registry
        self.registry.register_agent('policy_chunking', PolicyChunkingAgent())
        self.registry.register_agent('policy_extraction', PolicyExtractionAgent())
        self.registry.register_agent('policy_compliance', PolicyComplianceAgent())
    
    async def process_policy_document(self, policy_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process policy document using existing workflow orchestration."""
        workflow_context = WorkflowContext(
            workflow_id=f"policy_workflow_{policy_data.get('tenant_id', 'unknown')}",
            workflow_type="policy_processing",
            context_data=policy_data
        )
        
        # Define workflow steps using existing patterns
        workflow_steps = [
            {
                'agent_id': 'policy_chunking',
                'input_data': {
                    'policy_text': policy_data['policy_text'],
                    'tenant_id': policy_data['tenant_id'],
                    'policy_name': policy_data.get('policy_name', 'Unknown Policy')
                }
            },
            {
                'agent_id': 'policy_extraction',
                'input_data': {
                    'document_id': '${previous_result.document_id}',  # Reference previous step
                    'tenant_id': policy_data['tenant_id']
                }
            }
        ]
        
        # Execute workflow using existing supervisor
        results = []
        previous_result = None
        
        for step in workflow_steps:
            # Resolve references to previous results
            input_data = self._resolve_references(step['input_data'], previous_result)
            
            context = AgentContext(
                input_data=input_data,
                workflow_context=workflow_context
            )
            
            agent = self.registry.get_agent(step['agent_id'])
            result = agent.execute(context)
            
            results.append({
                'agent_id': step['agent_id'],
                'status': result.status,
                'data': result.data,
                'confidence': result.confidence
            })
            
            previous_result = result.data
            
            # Stop on error
            if result.status != 'success':
                break
        
        return {
            'workflow_id': workflow_context.workflow_id,
            'status': 'success' if all(r['status'] == 'success' for r in results) else 'error',
            'steps': results,
            'final_result': previous_result
        }
    
    def _resolve_references(self, input_data: Dict[str, Any], previous_result: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve references to previous step results."""
        if not previous_result:
            return input_data
        
        resolved = {}
        for key, value in input_data.items():
            if isinstance(value, str) and value.startswith('${previous_result.'):
                # Extract field name from reference
                field_name = value.replace('${previous_result.', '').replace('}', '')
                resolved[key] = previous_result.get(field_name, value)
            else:
                resolved[key] = value
        
        return resolved