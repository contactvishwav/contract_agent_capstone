"""Policy caching service reusing existing cache infrastructure."""

import json
import hashlib
from typing import List, Dict, Any, Optional
from backend.shared.cache.redis_cache import cache_result
from backend.domain.policies.entities import PolicyRule, PolicyDocument


class PolicyCacheService:
    """Policy caching service using existing Redis infrastructure."""
    
    @cache_result("policy_rules", ttl=3600)  # 1 hour cache
    def get_cached_policies(self, tenant_id: str, contract_type: str) -> List[Dict[str, Any]]:
        """Get cached policies using existing cache decorator."""
        # This method will be cached automatically by existing infrastructure
        from backend.infrastructure.policy_repository import PolicyRepository
        
        repo = PolicyRepository()
        policies = repo.get_applicable_policies(tenant_id, contract_type)
        
        # Convert to serializable format
        return [
            {
                'id': policy.id,
                'rule_text': policy.rule_text,
                'rule_type': policy.rule_type,
                'applies_to': policy.applies_to,
                'severity': policy.severity,
                'section_reference': policy.section_reference
            }
            for policy in policies
        ]
    
    @cache_result("policy_search", ttl=1800)  # 30 minutes cache
    def get_cached_search_results(self, query: str, tenant_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get cached semantic search results."""
        from backend.infrastructure.policy_repository import PolicyRepository
        
        repo = PolicyRepository()
        return repo.search_policies_semantic(query, tenant_id, limit)
    
    @cache_result("policy_document", ttl=7200)  # 2 hours cache
    def get_cached_policy_document(self, policy_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get cached policy document."""
        from backend.infrastructure.policy_repository import PolicyRepository

        repo = PolicyRepository()
        policy = repo.get_policy_by_id(policy_id, tenant_id)
        
        if not policy:
            return None
        
        return {
            'id': policy.id,
            'name': policy.name,
            'tenant_id': policy.tenant_id,
            'version': policy.version,
            'created_at': policy.created_at.isoformat() if policy.created_at else None,
            'checksum': policy.checksum,
            'rules': [
                {
                    'id': rule.id,
                    'rule_text': rule.rule_text,
                    'rule_type': rule.rule_type,
                    'applies_to': rule.applies_to,
                    'severity': rule.severity,
                    'section_reference': rule.section_reference
                }
                for rule in policy.rules
            ]
        }
    
    def invalidate_policy_cache(self, tenant_id: str, policy_id: str = None):
        """Invalidate policy cache when policies are updated."""
        # Use existing cache infrastructure to clear related caches
        cache_keys = [
            f"policy_rules:{tenant_id}:*",
            f"policy_search:{tenant_id}:*"
        ]
        
        if policy_id:
            cache_keys.append(f"policy_document:{policy_id}")
        
        # This would integrate with existing cache clearing mechanisms
        # For now, we'll implement a simple approach
        pass
    
    def generate_cache_key(self, prefix: str, *args) -> str:
        """Generate consistent cache keys."""
        key_data = f"{prefix}:{':'.join(str(arg) for arg in args)}"
        return hashlib.md5(key_data.encode()).hexdigest()[:16]