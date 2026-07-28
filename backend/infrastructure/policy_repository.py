"""Policy repository extending existing patterns."""

from typing import List, Dict, Any, Optional
from datetime import datetime
import hashlib
import json

from backend.shared.utils.contract_search_tool import graph, embedding
from backend.domain.policies.entities import PolicyDocument, PolicyRule


class PolicyRepository:
    """Extends existing contract repository patterns for policies."""
    
    def __init__(self):
        self.graph = graph  # Reuse existing connection
        self.embedding_service = embedding  # Reuse existing embedding service
    
    def store_policy_document(self, policy_data: Dict[str, Any]) -> str:
        """Store policy document using existing patterns."""
        try:
            policy_id = f"policy_{policy_data['tenant_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Generate document embedding
            policy_text = policy_data.get('full_text', '')
            document_embedding = []
            if policy_text:
                document_embedding = self.embedding_service.embed_query(policy_text[:1000])  # First 1000 chars
            
            # Calculate checksum for version control
            checksum = hashlib.md5(policy_text.encode()).hexdigest()
            
            # Store policy document
            doc_query = """
            CREATE (p:PolicyDocument {
                id: $policy_id,
                name: $name,
                tenant_id: $tenant_id,
                version: $version,
                document_type: $document_type,
                total_pages: $total_pages,
                checksum: $checksum,
                created_at: datetime(),
                updated_at: datetime(),
                embedding: $embedding
            })
            RETURN p.id as policy_id
            """
            
            result = self.graph.query(doc_query, {
                'policy_id': policy_id,
                'name': policy_data.get('name', 'Untitled Policy'),
                'tenant_id': policy_data['tenant_id'],
                'version': policy_data.get('version', '1.0'),
                'document_type': policy_data.get('document_type', 'compliance'),
                'total_pages': policy_data.get('total_pages', 0),
                'checksum': checksum,
                'embedding': document_embedding
            })
            
            return policy_id
            
        except Exception as e:
            raise Exception(f"Failed to store policy document: {e}")
    
    def get_policy_by_id(self, policy_id: str, tenant_id: str) -> Optional[PolicyDocument]:
        """Get policy document by ID - Enforces multi-tenant isolation."""
        try:
            query = """
            MATCH (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})-[:HAS_RULE]->(r:PolicyRule)
            RETURN p.id as id, p.name as name, p.tenant_id as tenant_id,
                   p.version as version, p.created_at as created_at,
                   p.updated_at as updated_at, p.checksum as checksum,
                   collect({
                       id: r.id,
                       rule_text: r.rule_text,
                       rule_type: r.rule_type,
                       applies_to: r.applies_to,
                       severity: r.severity,
                       section_reference: r.section_reference
                   }) as rules
            """

            result = self.graph.query(query, {'policy_id': policy_id, 'tenant_id': tenant_id})
            
            if result:
                record = result[0]
                rules = [
                    PolicyRule(
                        id=rule['id'],
                        rule_text=rule['rule_text'],
                        rule_type=rule['rule_type'],
                        applies_to=rule['applies_to'],
                        severity=rule['severity'],
                        section_reference=rule['section_reference']
                    )
                    for rule in record['rules']
                ]
                
                return PolicyDocument(
                    id=record['id'],
                    name=record['name'],
                    tenant_id=record['tenant_id'],
                    version=record['version'],
                    rules=rules,
                    created_at=record['created_at'],
                    checksum=record['checksum']
                )
            
            return None
            
        except Exception as e:
            raise Exception(f"Failed to get policy: {e}")
    
    def get_policies_by_tenant(self, tenant_id: str) -> List[PolicyDocument]:
        """Get all policies for a tenant."""
        try:
            query = """
            MATCH (p:PolicyDocument {tenant_id: $tenant_id})
            OPTIONAL MATCH (p)-[:HAS_RULE]->(r:PolicyRule)
            RETURN p.id as id, p.name as name, p.version as version,
                   p.created_at as created_at, p.checksum as checksum,
                   count(r) as rule_count
            ORDER BY p.created_at DESC
            """
            
            result = self.graph.query(query, {'tenant_id': tenant_id})
            
            policies = []
            for record in result:
                # Get rules separately for each policy
                rules = self._get_policy_rules(record['id'], tenant_id)
                
                policy = PolicyDocument(
                    id=record['id'],
                    name=record['name'],
                    tenant_id=tenant_id,
                    version=record['version'],
                    rules=rules,
                    created_at=record['created_at'],
                    checksum=record['checksum']
                )
                policies.append(policy)
            
            return policies
            
        except Exception as e:
            raise Exception(f"Failed to get tenant policies: {e}")
    
    def get_applicable_policies(self, tenant_id: str, contract_type: str) -> List[PolicyRule]:
        """Get policies applicable to specific contract type."""
        try:
            query = """
            MATCH (p:PolicyDocument {tenant_id: $tenant_id})-[:HAS_RULE]->(r:PolicyRule)
            WHERE $contract_type IN r.applies_to OR 'general' IN r.applies_to
            RETURN r.id as id, r.rule_text as rule_text, r.rule_type as rule_type,
                   r.applies_to as applies_to, r.severity as severity,
                   r.section_reference as section_reference
            ORDER BY 
                CASE r.severity 
                    WHEN 'CRITICAL' THEN 1 
                    WHEN 'HIGH' THEN 2 
                    WHEN 'MEDIUM' THEN 3 
                    ELSE 4 
                END
            """
            
            result = self.graph.query(query, {
                'tenant_id': tenant_id,
                'contract_type': contract_type
            })
            
            policies = []
            for record in result:
                policy = PolicyRule(
                    id=record['id'],
                    rule_text=record['rule_text'],
                    rule_type=record['rule_type'],
                    applies_to=record['applies_to'],
                    severity=record['severity'],
                    section_reference=record['section_reference']
                )
                policies.append(policy)
            
            return policies
            
        except Exception as e:
            raise Exception(f"Failed to get applicable policies: {e}")
    
    def search_policies_semantic(self, query_text: str, tenant_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search policies using semantic similarity."""
        try:
            # Generate query embedding
            query_embedding = self.embedding_service.embed_query(query_text)
            
            # Search using existing vector similarity patterns
            search_query = """
            MATCH (p:PolicyDocument {tenant_id: $tenant_id})-[:HAS_RULE]->(r:PolicyRule)
            WHERE p.embedding IS NOT NULL
            WITH p, r, gds.similarity.cosine(p.embedding, $query_embedding) AS similarity
            WHERE similarity > 0.7
            RETURN p.id as policy_id, p.name as policy_name,
                   r.id as rule_id, r.rule_text as rule_text,
                   r.severity as severity, similarity
            ORDER BY similarity DESC
            LIMIT $limit
            """
            
            result = self.graph.query(search_query, {
                'tenant_id': tenant_id,
                'query_embedding': query_embedding,
                'limit': limit
            })
            
            return [dict(record) for record in result]
            
        except Exception as e:
            raise Exception(f"Failed to search policies: {e}")
    
    def _get_policy_rules(self, policy_id: str, tenant_id: str) -> List[PolicyRule]:
        """Get rules for a specific policy - Enforces multi-tenant isolation."""
        query = """
        MATCH (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})-[:HAS_RULE]->(r:PolicyRule)
        RETURN r.id as id, r.rule_text as rule_text, r.rule_type as rule_type,
               r.applies_to as applies_to, r.severity as severity,
               r.section_reference as section_reference
        """

        result = self.graph.query(query, {'policy_id': policy_id, 'tenant_id': tenant_id})
        
        rules = []
        for record in result:
            rule = PolicyRule(
                id=record['id'],
                rule_text=record['rule_text'],
                rule_type=record['rule_type'],
                applies_to=record['applies_to'],
                severity=record['severity'],
                section_reference=record['section_reference']
            )
            rules.append(rule)
        
        return rules
    
    def update_policy_version(self, policy_id: str, tenant_id: str, new_version: str, updated_rules: List[PolicyRule]) -> bool:
        """Update policy version with new rules - Enforces multi-tenant isolation."""
        try:
            # Archive current version - scoped to this tenant. If nothing
            # matches (wrong tenant, or doesn't exist), stop here rather than
            # creating an orphaned "new version" of a policy that isn't ours.
            archive_query = """
            MATCH (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})
            SET p.archived_at = datetime(),
                p.active = false
            RETURN p.id as matched_id
            """
            archived = self.graph.query(archive_query, {'policy_id': policy_id, 'tenant_id': tenant_id})
            if not archived:
                return False

            # Create new version
            new_policy_id = f"{policy_id}_v{new_version}"

            # Copy policy document with new version
            copy_query = """
            MATCH (old:PolicyDocument {id: $old_policy_id, tenant_id: $tenant_id})
            CREATE (new:PolicyDocument {
                id: $new_policy_id,
                name: old.name,
                tenant_id: old.tenant_id,
                version: $new_version,
                document_type: old.document_type,
                total_pages: old.total_pages,
                checksum: $new_checksum,
                created_at: datetime(),
                updated_at: datetime(),
                active: true
            })
            """

            new_checksum = hashlib.md5(json.dumps([r.__dict__ for r in updated_rules]).encode()).hexdigest()

            self.graph.query(copy_query, {
                'old_policy_id': policy_id,
                'tenant_id': tenant_id,
                'new_policy_id': new_policy_id,
                'new_version': new_version,
                'new_checksum': new_checksum
            })

            # Add updated rules
            for rule in updated_rules:
                rule_query = """
                MATCH (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})
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

                self.graph.query(rule_query, {
                    'policy_id': new_policy_id,
                    'tenant_id': tenant_id,
                    'rule_id': rule.id,
                    'rule_text': rule.rule_text,
                    'rule_type': rule.rule_type,
                    'applies_to': rule.applies_to,
                    'severity': rule.severity,
                    'section_reference': rule.section_reference
                })

            return True

        except Exception as e:
            raise Exception(f"Failed to update policy version: {e}")
    
    def delete_policy(self, policy_id: str, tenant_id: str) -> bool:
        """Soft delete policy by marking as inactive - Enforces multi-tenant isolation."""
        try:
            query = """
            MATCH (p:PolicyDocument {id: $policy_id, tenant_id: $tenant_id})
            SET p.active = false, p.deleted_at = datetime()
            RETURN p.id as matched_id
            """

            result = self.graph.query(query, {'policy_id': policy_id, 'tenant_id': tenant_id})
            return bool(result)

        except Exception as e:
            raise Exception(f"Failed to delete policy: {e}")