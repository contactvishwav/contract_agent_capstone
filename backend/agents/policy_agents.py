"""Policy agents extending existing infrastructure."""

import asyncio
import uuid
from typing import Dict, Any, List
from backend.agents.supervisor.interfaces import IAgent, AgentContext, AgentResult
from backend.infrastructure.chunking.factory import ChunkingFactory
from backend.infrastructure.chunking.storage_service import ChunkingStorageService
from backend.shared.utils.contract_search_tool import graph
from backend.domain.policies.entities import PolicyDocument, PolicyRule, PolicyViolation


class PolicyChunkingAgent(IAgent):
    """Reuses existing chunking infrastructure for policy documents."""
    
    def __init__(self):
        self.chunking_factory = ChunkingFactory()
        self.storage_service = ChunkingStorageService()
    
    def execute(self, context: AgentContext) -> AgentResult:
        """Execute policy document chunking."""
        try:
            policy_text = context.input_data['policy_text']
            tenant_id = context.input_data['tenant_id']
            policy_name = context.input_data.get('policy_name', 'Unknown Policy')
            
            # Use existing policy chunking strategy
            strategy = self.chunking_factory.create_strategy('policy')
            chunks = strategy.chunk_document(policy_text, {'document_type': 'policy'})
            
            # Store using existing infrastructure
            document_id = f"policy_{tenant_id}_{uuid.uuid4().hex[:8]}"
            
            # Use existing async storage (convert to sync for now)
            import asyncio
            result = asyncio.run(self.storage_service.store_chunks(
                document_id, chunks, {'policy_name': policy_name, 'tenant_id': tenant_id}
            ))
            
            return AgentResult(
                status='success',
                data={
                    'document_id': document_id,
                    'chunks_created': len(chunks),
                    'storage_result': result
                },
                confidence=0.9
            )
            
        except Exception as e:
            return AgentResult(
                status='error',
                data={'error': str(e)},
                confidence=0.0
            )
    
    def get_capabilities(self) -> List[str]:
        return ['policy_chunking', 'document_processing', 'rule_extraction']


class PolicyExtractionAgent(IAgent):
    """Extends existing clause extraction for policy rules."""
    
    def execute(self, context: AgentContext) -> AgentResult:
        """Extract policy rules from chunks."""
        try:
            document_id = context.input_data['document_id']
            tenant_id = context.input_data['tenant_id']
            
            # Get chunks using existing storage service
            storage_service = ChunkingStorageService()
            chunks = asyncio.run(storage_service.get_chunks(document_id))
            
            # Extract policy rules from chunks
            policy_rules = []
            for chunk in chunks:
                if chunk.get('chunk_type') == 'policy_rule':
                    rule = PolicyRule(
                        id=f"rule_{uuid.uuid4().hex[:8]}",
                        rule_text=chunk['content'],
                        rule_type=chunk.get('rule_type', 'general'),
                        applies_to=chunk.get('applies_to', ['general']),
                        severity=chunk.get('severity', 'MEDIUM'),
                        section_reference=chunk.get('section_title', 'Unknown'),
                        exceptions=[]
                    )
                    policy_rules.append(rule)
            
            # Store rules in Neo4j using existing graph connection
            self._store_policy_rules(policy_rules, document_id, tenant_id)
            
            return AgentResult(
                status='success',
                data={
                    'rules_extracted': len(policy_rules),
                    'policy_rules': [rule.__dict__ for rule in policy_rules]
                },
                confidence=0.85
            )
            
        except Exception as e:
            return AgentResult(
                status='error',
                data={'error': str(e)},
                confidence=0.0
            )
    
    def _store_policy_rules(self, rules: List[PolicyRule], document_id: str, tenant_id: str):
        """Store policy rules in Neo4j using existing patterns."""
        for rule in rules:
            query = """
            MERGE (p:PolicyDocument {id: $document_id})
            SET p.tenant_id = $tenant_id
            
            CREATE (r:PolicyRule {
                id: $rule_id,
                rule_text: $rule_text,
                rule_type: $rule_type,
                applies_to: $applies_to,
                severity: $severity,
                section_reference: $section_reference,
                created_at: datetime()
            })
            
            CREATE (p)-[:HAS_RULE]->(r)
            """
            
            graph.query(query, {
                'document_id': document_id,
                'tenant_id': tenant_id,
                'rule_id': rule.id,
                'rule_text': rule.rule_text,
                'rule_type': rule.rule_type,
                'applies_to': rule.applies_to,
                'severity': rule.severity,
                'section_reference': rule.section_reference
            })
    
    def get_capabilities(self) -> List[str]:
        return ['policy_extraction', 'rule_identification', 'legal_analysis']


class PolicyComplianceAgent(IAgent):
    """
    Policy compliance checking for the standalone /api/policies/compliance/
    check route. Delegates rule resolution and evaluation to the same
    engine the main analysis pipeline uses (policy_rule_resolver.
    get_applicable_rules + PolicyEvaluationService in
    backend/agents/intelligence_tools.py's PolicyCheckerTool) rather than
    maintaining a second, independent evaluator - this used to be its own
    3-term keyword matcher (even more primitive than the main pipeline's
    former 6-category dict), explicitly marked "can be enhanced with LLM"
    in its own comment. Now there is one evaluation path, not two.
    """

    def execute(self, context: AgentContext) -> AgentResult:
        """Check contract compliance against stored (or default) policies."""
        try:
            from backend.agents.policy_rule_resolver import get_applicable_rules
            from backend.agents.policy_evaluation_service import PolicyEvaluationService
            from backend.agents.llm_extraction_service import get_default_llm

            tenant_id = context.input_data['tenant_id']
            contract_clauses = context.input_data['clauses']
            contract_type = context.input_data.get('contract_type', 'general')

            applicable_rules = get_applicable_rules(tenant_id, contract_type)
            evaluation_service = PolicyEvaluationService(get_default_llm())

            violations = []
            for clause in contract_clauses:
                clause_type = clause.get('type', clause.get('clause_type', 'general'))
                clause_content = clause.get('content', '')
                for v in evaluation_service.evaluate_clause(clause_type, clause_content, applicable_rules):
                    violations.append(PolicyViolation(
                        policy_rule_id=v['rule_id'],
                        clause_content=clause_content,
                        violation_type='policy_violation',
                        severity=v['severity'],
                        message=v['issue'],
                        recommendation=v['suggested_fix'],
                        confidence=v['confidence'],
                    ))

            return AgentResult(
                status='success',
                data={
                    'violations_found': len(violations),
                    'violations': [v.__dict__ for v in violations],
                    'policies_checked': len(applicable_rules)
                },
                confidence=0.9
            )

        except Exception as e:
            return AgentResult(
                status='error',
                data={'error': str(e)},
                confidence=0.0
            )

    def get_capabilities(self) -> List[str]:
        return ['policy_compliance', 'violation_detection', 'risk_assessment']