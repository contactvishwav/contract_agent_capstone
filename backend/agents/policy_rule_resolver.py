"""
Resolves which PolicyRule set applies for a given tenant/contract_type -
the tenant's own uploaded policy rules if any exist, otherwise the small
default rule set (backend/domain/policies/default_rules.py). Shared by
PolicyCheckerTool (the main analysis pipeline) and PolicyComplianceAgent
(the standalone /api/policies/compliance/check route) so there is one
source of truth for "which rules apply here" instead of two independently
drifting copies.
"""

from typing import List, Optional

from backend.domain.policies.entities import PolicyRule
from backend.domain.policies.default_rules import filter_default_rules_by_contract_type
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


def get_applicable_rules(tenant_id: Optional[str], contract_type: str = "general") -> List[PolicyRule]:
    """
    Tenant's own uploaded policy rules (via PolicyRepository), if any exist,
    otherwise the default rule set. Any repository failure degrades to the
    default set rather than raising - an infrastructure hiccup shouldn't
    silently skip policy checking altogether - but is logged so it's not a
    silent substitution.
    """
    if not tenant_id:
        return filter_default_rules_by_contract_type(contract_type)

    try:
        from backend.infrastructure.policy_repository import PolicyRepository
        repo = PolicyRepository()
        tenant_policies = repo.get_policies_by_tenant(tenant_id)
        if not tenant_policies:
            return filter_default_rules_by_contract_type(contract_type)
        return repo.get_applicable_policies(tenant_id, contract_type)
    except Exception as e:
        logger.warning(f"Falling back to default policy rules for tenant {tenant_id}: {e}")
        return filter_default_rules_by_contract_type(contract_type)
