"""Policy workflow supervisor using existing infrastructure."""

from typing import Dict, Any, List
from backend.agents.supervisor.supervisor_agent import SupervisorAgent
from backend.agents.supervisor.agent_registry import AgentRegistry
from backend.agents.supervisor.quality_manager import QualityManager
from backend.agents.supervisor.interfaces import AgentContext, AgentResult
from backend.agents.policy_agents import PolicyChunkingAgent, PolicyExtractionAgent, PolicyComplianceAgent
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType
from backend.infrastructure.error_tracker import ErrorTracker, ErrorCategory
from backend.infrastructure.content_validator import ContentValidationService
from backend.shared.monitoring.performance_monitor import track_performance
from backend.shared.cache.redis_cache import cache_result


class PolicyWorkflowSupervisor:
    """Orchestrates policy processing using existing supervisor infrastructure."""

    def __init__(self):
        self.registry = AgentRegistry()
        self.supervisor = SupervisorAgent(self.registry, QualityManager())
        self.audit_logger = AuditLogger()
        self.error_tracker = ErrorTracker()
        self.content_validator = ContentValidationService()

        # Register policy agents using existing registry
        self.registry.register_agent('policy_validation', PolicyValidationAgent())
        self.registry.register_agent('policy_chunking', PolicyChunkingAgent())
        self.registry.register_agent('policy_extraction', PolicyExtractionAgent())
        self.registry.register_agent('policy_compliance', PolicyComplianceAgent())
    
    @track_performance("policy_workflow")
    async def orchestrate_policy_processing(self, policy_data: Dict[str, Any]) -> Dict[str, Any]:
        """Orchestrate complete policy processing workflow."""
        try:
            # Log policy processing start
            self.audit_logger.log_event(
                event_type=AuditEventType.ANALYSIS_REQUEST,
                resource_id=policy_data.get('policy_name', 'unknown'),
                action="policy_processing_started",
                tenant_id=policy_data.get('tenant_id'),
                metadata={'policy_size': len(policy_data.get('policy_text', ''))}
            )
            
            # Define workflow steps using existing supervisor patterns
            workflow_steps = [
                ('policy_validation', {
                    'policy_text': policy_data['policy_text'],
                    'tenant_id': policy_data['tenant_id']
                }),
                ('policy_chunking', {
                    'policy_text': policy_data['policy_text'],
                    'tenant_id': policy_data['tenant_id'],
                    'policy_name': policy_data.get('policy_name', 'Unknown')
                }),
                ('policy_extraction', {
                    'document_id': 'from_previous_step',
                    'tenant_id': policy_data['tenant_id']
                })
            ]
            
            # Execute workflow using existing supervisor
            results = []
            previous_result = None
            
            for step_name, step_data in workflow_steps:
                # Update step data with previous result if needed
                if previous_result and 'from_previous_step' in str(step_data):
                    if step_name == 'policy_extraction':
                        step_data['document_id'] = previous_result.data.get('document_id')
                
                # Execute step
                agent = self.registry.get_agent(step_name)
                context = AgentContext(input_data=step_data, workflow_context=None)
                
                result = agent.execute(context)
                results.append({
                    'step': step_name,
                    'status': result.status,
                    'data': result.data,
                    'confidence': result.confidence
                })
                
                if result.status != 'success':
                    # Log error using existing error tracker
                    self.error_tracker.track_error(
                        error_id=f"policy_{step_name}_error",
                        category=ErrorCategory.PROCESSING_ERROR,
                        message=f"Policy {step_name} failed",
                        details=result.data,
                        tenant_id=policy_data['tenant_id']
                    )
                    break
                
                previous_result = result
            
            # Log completion
            self.audit_logger.log_event(
                event_type=AuditEventType.ANALYSIS_REQUEST,
                resource_id=policy_data.get('policy_name', 'unknown'),
                action="policy_processing_completed",
                tenant_id=policy_data.get('tenant_id'),
                metadata={'steps_completed': len(results)}
            )
            
            return {
                'success': all(r['status'] == 'success' for r in results),
                'workflow_results': results,
                'final_policy_id': previous_result.data.get('document_id') if previous_result else None
            }
            
        except Exception as e:
            # Track error using existing infrastructure
            self.error_tracker.track_error(
                error_id="policy_workflow_error",
                category=ErrorCategory.SYSTEM_ERROR,
                message=f"Policy workflow failed: {str(e)}",
                details={'policy_data': policy_data},
                tenant_id=policy_data.get('tenant_id')
            )
            
            return {
                'success': False,
                'error': str(e),
                'workflow_results': []
            }


class PolicyValidationAgent:
    """Policy validation agent using existing content validator."""
    
    def __init__(self):
        self.content_validator = ContentValidationService()
    
    def execute(self, context: AgentContext) -> AgentResult:
        """Validate policy document using existing validation infrastructure."""
        try:
            policy_text = context.input_data['policy_text']

            # Use existing content validation patterns.
            # ContentValidationService.validate_file_upload takes a single data
            # dict (file_size/filename), not discrete kwargs, and returns
            # "is_valid"/"results" keys, not "valid"/"errors".
            validation_result = self.content_validator.validate_file_upload({
                "file_size": len(policy_text.encode()),
                "filename": "policy.txt"
            })

            if not validation_result['is_valid']:
                failed_checks = [r for r in validation_result['results'] if not r['is_valid']]
                return AgentResult(
                    status='error',
                    data={'validation_errors': failed_checks},
                    confidence=0.0
                )
            
            # Additional policy-specific validation
            policy_validation = self._validate_policy_structure(policy_text)
            
            return AgentResult(
                status='success',
                data={
                    'content_validation': validation_result,
                    'policy_validation': policy_validation
                },
                confidence=0.9
            )
            
        except Exception as e:
            return AgentResult(
                status='error',
                data={'error': str(e)},
                confidence=0.0
            )
    
    def _validate_policy_structure(self, policy_text: str) -> Dict[str, Any]:
        """Validate policy document structure."""
        validation = {
            'has_sections': bool(len([line for line in policy_text.split('\n') if line.strip().isupper()]) > 0),
            'has_rules': bool(any(word in policy_text.lower() for word in ['shall', 'must', 'required', 'prohibited'])),
            'min_length': len(policy_text) > 100,
            'structure_score': 0.0
        }
        
        # Calculate structure score
        score = 0.0
        if validation['has_sections']:
            score += 0.4
        if validation['has_rules']:
            score += 0.4
        if validation['min_length']:
            score += 0.2
        
        validation['structure_score'] = score
        validation['valid'] = score >= 0.6
        
        return validation
    
    def get_capabilities(self) -> List[str]:
        return ['policy_validation', 'content_validation', 'structure_analysis']


class PolicyCacheManager:
    """Policy caching using existing Redis infrastructure."""
    
    @cache_result("policy_search", ttl=1800)
    def search_policies_cached(self, query: str, tenant_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Cached policy search using existing Redis cache."""
        from backend.infrastructure.policy_repository import PolicyRepository
        
        repo = PolicyRepository()
        return repo.search_policies_semantic(query, tenant_id, limit)
    
    @cache_result("tenant_policies", ttl=3600)
    def get_tenant_policies_cached(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Cached tenant policies using existing Redis cache."""
        from backend.infrastructure.policy_repository import PolicyRepository
        
        repo = PolicyRepository()
        policies = repo.get_policies_by_tenant(tenant_id)
        
        # Convert to serializable format for caching
        return [
            {
                'id': policy.id,
                'name': policy.name,
                'version': policy.version,
                'rules_count': len(policy.rules),
                'created_at': policy.created_at.isoformat() if policy.created_at else None
            }
            for policy in policies
        ]