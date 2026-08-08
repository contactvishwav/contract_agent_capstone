"""Policy service layer following DDD patterns."""

from typing import Dict, Any, List, Optional
from backend.domain.policies.entities import PolicyDocument, PolicyRule, PolicyViolation
from backend.infrastructure.policy_repository import PolicyRepository
from backend.infrastructure.policy_cache_service import PolicyCacheService
from backend.infrastructure.policy_validation_service import PolicyValidationService
from backend.infrastructure.policy_audit_service import PolicyAuditService
from backend.infrastructure.error_tracker import ErrorCategory, ErrorContext, ErrorSeverity
from backend.agents.policy_workflow_orchestrator import PolicyWorkflowOrchestrator


class PolicyService:
    """Application service for policy management following DDD patterns."""
    
    def __init__(self):
        self.repository = PolicyRepository()
        self.cache_service = PolicyCacheService()
        self.validation_service = PolicyValidationService()
        self.audit_service = PolicyAuditService()
        self.orchestrator = PolicyWorkflowOrchestrator()
    
    async def upload_and_process_policy(self, policy_data: Dict[str, Any], user_id: str = None) -> Dict[str, Any]:
        """Upload and process policy document with full validation and audit."""
        try:
            # Step 1: Validate policy
            validation_result = self.validation_service.validate_policy_upload(policy_data)
            if not validation_result.passed:
                self.audit_service.error_tracker.track_error(
                    error=ValueError(validation_result.message),
                    category=ErrorCategory.VALIDATION_ERROR,
                    severity=ErrorSeverity.LOW,
                    context=ErrorContext(
                        operation="policy_upload_validation",
                        tenant_id=policy_data.get('tenant_id'),
                        metadata={'policy_name': policy_data.get('policy_name')},
                    ),
                )
                return {
                    'success': False,
                    'error': validation_result.message,
                    'validation_details': validation_result.details
                }
            
            # Step 2: Process using orchestrator
            processing_result = await self.orchestrator.process_policy_document(policy_data)
            
            if processing_result['status'] != 'success':
                return {
                    'success': False,
                    'error': 'Policy processing failed',
                    'processing_details': processing_result
                }
            
            # Step 3: Audit logging
            policy_id = processing_result['final_result'].get('document_id')
            self.audit_service.log_policy_upload(
                policy_data['tenant_id'], 
                policy_id, 
                policy_data['policy_name'],
                user_id
            )
            self.audit_service.log_policy_processing(
                policy_data['tenant_id'], 
                policy_id, 
                processing_result
            )
            
            # Step 4: Invalidate cache
            self.cache_service.invalidate_policy_cache(policy_data['tenant_id'], policy_id)
            
            return {
                'success': True,
                'policy_id': policy_id,
                'validation_score': validation_result.score,
                'workflow_id': processing_result['workflow_id'],
                'processing_steps': len(processing_result['steps']),
                'message': f'Policy "{policy_data["policy_name"]}" processed successfully'
            }
            
        except Exception as e:
            self.audit_service.error_tracker.track_error(
                error=e,
                category=ErrorCategory.PROCESSING_ERROR,
                severity=ErrorSeverity.HIGH,
                context=ErrorContext(
                    operation="policy_upload_processing",
                    tenant_id=policy_data.get('tenant_id'),
                    metadata={'policy_name': policy_data.get('policy_name')},
                ),
            )
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_tenant_policies(self, tenant_id: str, use_cache: bool = True) -> Dict[str, Any]:
        """Get all policies for a tenant with caching."""
        try:
            policies = []
            source = 'database'
            
            # Try cache first if enabled
            if use_cache:
                try:
                    cached_result = self.cache_service.get_cached_policy_document(f"tenant_{tenant_id}", tenant_id)
                    if cached_result:
                        policies = cached_result if isinstance(cached_result, list) else [cached_result]
                        source = 'cache'
                except:
                    pass  # Fall through to database
            
            # Get from database if not cached
            if not policies:
                policy_entities = self.repository.get_policies_by_tenant(tenant_id)
                policies = [
                    {
                        'id': policy.id,
                        'name': policy.name,
                        'version': policy.version,
                        'rules_count': len(policy.rules),
                        'created_at': policy.created_at.isoformat() if policy.created_at else None
                    }
                    for policy in policy_entities
                ]
            
            # Log search
            self.audit_service.log_policy_search(tenant_id, f"tenant_policies:{tenant_id}", len(policies))
            
            return {
                'success': True,
                'tenant_id': tenant_id,
                'policies_count': len(policies),
                'policies': policies,
                'source': source
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def check_contract_compliance(self, tenant_id: str, contract_clauses: List[Dict[str, Any]], 
                                contract_type: str = 'general', contract_id: str = None) -> Dict[str, Any]:
        """Check contract compliance against policies."""
        try:
            # Get applicable policies (with caching)
            applicable_policies = self.cache_service.get_cached_policies(tenant_id, contract_type)
            
            if not applicable_policies:
                # Fallback to database
                policy_entities = self.repository.get_applicable_policies(tenant_id, contract_type)
                applicable_policies = [
                    {
                        'id': policy.id,
                        'rule_text': policy.rule_text,
                        'rule_type': policy.rule_type,
                        'applies_to': policy.applies_to,
                        'severity': policy.severity,
                        'section_reference': policy.section_reference
                    }
                    for policy in policy_entities
                ]
            
            # Check compliance
            violations = []
            for clause in contract_clauses:
                clause_violations = self._check_clause_against_policies(clause, applicable_policies)
                violations.extend(clause_violations)
            
            compliance_result = {
                'violations_found': len(violations),
                'policies_checked': len(applicable_policies),
                'violations': violations,
                'compliance_score': self._calculate_compliance_score(violations)
            }
            
            # Audit compliance check
            if contract_id:
                self.audit_service.log_policy_compliance_check(tenant_id, contract_id, compliance_result)
            
            return {
                'success': True,
                'compliance_check': compliance_result
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def search_policies(self, tenant_id: str, query: str, limit: int = 10, user_id: str = None) -> Dict[str, Any]:
        """Search policies with caching and audit."""
        try:
            # Try cached search first
            results = self.cache_service.get_cached_search_results(query, tenant_id, limit)
            
            if not results:
                # Fallback to database search
                results = self.repository.search_policies_semantic(query, tenant_id, limit)
            
            # Audit search
            self.audit_service.log_policy_search(tenant_id, query, len(results), user_id)
            
            return {
                'success': True,
                'query': query,
                'results_count': len(results),
                'results': results
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _check_clause_against_policies(self, clause: Dict[str, Any], policies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Check single clause against applicable policies."""
        violations = []
        clause_content = clause.get('content', '').lower()
        clause_type = clause.get('type', 'general')
        
        for policy in policies:
            if clause_type not in policy['applies_to'] and 'general' not in policy['applies_to']:
                continue
            
            violation = self._evaluate_policy_rule(clause, policy)
            if violation:
                violations.append(violation)
        
        return violations
    
    def _evaluate_policy_rule(self, clause: Dict[str, Any], policy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Evaluate clause against specific policy rule."""
        clause_content = clause.get('content', '').lower()
        rule_text = policy['rule_text'].lower()
        
        if policy['rule_type'] == 'prohibited':
            # Check for prohibited terms
            prohibited_terms = ['unlimited liability', 'immediate termination', 'no notice']
            for term in prohibited_terms:
                if term in clause_content and term in rule_text:
                    return {
                        'policy_rule_id': policy['id'],
                        'clause_content': clause.get('content', ''),
                        'violation_type': 'prohibited_term',
                        'severity': policy['severity'],
                        'message': f"Clause contains prohibited term: {term}",
                        'recommendation': f"Remove or modify '{term}' to comply with policy",
                        'confidence': 0.8
                    }
        
        elif policy['rule_type'] == 'mandatory':
            # Check for missing mandatory terms
            mandatory_terms = ['liability cap', 'notice period', 'governing law']
            for term in mandatory_terms:
                if term in rule_text and term not in clause_content:
                    return {
                        'policy_rule_id': policy['id'],
                        'clause_content': clause.get('content', ''),
                        'violation_type': 'missing_mandatory',
                        'severity': policy['severity'],
                        'message': f"Clause missing mandatory term: {term}",
                        'recommendation': f"Add '{term}' to comply with policy",
                        'confidence': 0.7
                    }
        
        return None
    
    def _calculate_compliance_score(self, violations: List[Dict[str, Any]]) -> float:
        """Calculate compliance score based on violations."""
        if not violations:
            return 1.0
        
        # Weight violations by severity
        severity_weights = {'CRITICAL': 1.0, 'HIGH': 0.7, 'MEDIUM': 0.4, 'LOW': 0.2}
        total_weight = sum(severity_weights.get(v.get('severity', 'LOW'), 0.2) for v in violations)
        
        # Calculate score (lower is better for violations)
        max_possible_weight = len(violations) * 1.0  # If all were critical
        compliance_score = max(0.0, 1.0 - (total_weight / max_possible_weight))
        
        return compliance_score