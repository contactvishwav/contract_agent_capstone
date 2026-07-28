"""Policy management API extending existing patterns."""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from backend.governance.rbac import Permission, requires_permission
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from backend.application.services.policy_service import PolicyService
from backend.infrastructure.policy_cache_service import PolicyCacheService
from backend.infrastructure.policy_audit_service import PolicyAuditService
from backend.infrastructure.policy_repository import PolicyRepository
from backend.agents.policy_agents import PolicyComplianceAgent
from backend.shared.models.agent_models import AgentContext

router = APIRouter(prefix="/api/policies", tags=["Policy Management"])


class PolicyComplianceRequest(BaseModel):
    tenant_id: str
    contract_clauses: List[Dict[str, Any]]
    contract_type: str = "general"


class PolicySearchRequest(BaseModel):
    tenant_id: str
    query: str
    contract_type: Optional[str] = None
    limit: int = 10


@router.post("/upload", dependencies=[Depends(requires_permission(Permission.MANAGE_POLICIES))])
async def upload_policy_document(
    file: UploadFile = File(...),
    tenant_id: str = Form(...),
    policy_name: str = Form(...),
    policy_type: str = Form("compliance"),
    version: str = Form("1.0")
):
    """Upload and process policy document using service layer."""
    try:
        # Read file content
        content = await file.read()
        policy_text = content.decode('utf-8')
        
        policy_data = {
            'policy_text': policy_text,
            'tenant_id': tenant_id,
            'policy_name': policy_name,
            'policy_type': policy_type,
            'version': version
        }
        
        # Use service layer
        policy_service = PolicyService()
        result = await policy_service.upload_and_process_policy(policy_data)
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result['error'])
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tenant/{tenant_id}", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_tenant_policies(tenant_id: str):
    """Get all policies for a tenant using cache."""
    try:
        cache_service = PolicyCacheService()
        audit_service = PolicyAuditService()
        
        # Try cache first
        try:
            cached_policies = cache_service.get_cached_policy_document(f"tenant_{tenant_id}", tenant_id)
            if cached_policies:
                return {
                    'success': True,
                    'tenant_id': tenant_id,
                    'policies': cached_policies,
                    'source': 'cache'
                }
        except:
            pass  # Fall through to database
        
        # Get from database
        repository = PolicyRepository()
        policies = repository.get_policies_by_tenant(tenant_id)
        
        # Log search
        audit_service.log_policy_search(tenant_id, f"tenant_policies:{tenant_id}", len(policies))
        
        return {
            'success': True,
            'tenant_id': tenant_id,
            'policies_count': len(policies),
            'policies': [
                {
                    'id': policy.id,
                    'name': policy.name,
                    'version': policy.version,
                    'rules_count': len(policy.rules),
                    'created_at': policy.created_at.isoformat() if policy.created_at else None
                }
                for policy in policies
            ],
            'source': 'database'
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{policy_id}", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_policy_details(policy_id: str, tenant_id: str):
    """Get detailed policy information."""
    try:
        repository = PolicyRepository()
        policy = repository.get_policy_by_id(policy_id, tenant_id)
        
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        
        return {
            'success': True,
            'policy': {
                'id': policy.id,
                'name': policy.name,
                'tenant_id': policy.tenant_id,
                'version': policy.version,
                'created_at': policy.created_at.isoformat() if policy.created_at else None,
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
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compliance/check", dependencies=[Depends(requires_permission(Permission.ANALYZE))])
async def check_policy_compliance(request: PolicyComplianceRequest):
    """Check contract compliance against policies using existing agent."""
    try:
        compliance_agent = PolicyComplianceAgent()
        
        context = AgentContext(
            input_data={
                'tenant_id': request.tenant_id,
                'clauses': request.contract_clauses,
                'contract_type': request.contract_type
            },
            workflow_context=None
        )
        
        result = compliance_agent.execute(context)
        
        if result.status != 'success':
            raise HTTPException(status_code=500, detail=result.data.get('error'))
        
        return {
            'success': True,
            'compliance_check': {
                'violations_found': result.data['violations_found'],
                'policies_checked': result.data['policies_checked'],
                'violations': result.data['violations']
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", dependencies=[Depends(requires_permission(Permission.ANALYZE))])
async def search_policies(request: PolicySearchRequest):
    """Search policies using semantic similarity."""
    try:
        repository = PolicyRepository()
        
        # Use existing semantic search
        results = repository.search_policies_semantic(
            request.query,
            request.tenant_id,
            request.limit
        )
        
        return {
            'success': True,
            'query': request.query,
            'results_count': len(results),
            'results': results
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/applicable/{tenant_id}/{contract_type}")
async def get_applicable_policies(tenant_id: str, contract_type: str):
    """Get policies applicable to specific contract type."""
    try:
        repository = PolicyRepository()
        policies = repository.get_applicable_policies(tenant_id, contract_type)
        
        return {
            'success': True,
            'tenant_id': tenant_id,
            'contract_type': contract_type,
            'applicable_policies': [
                {
                    'id': policy.id,
                    'rule_text': policy.rule_text[:100] + '...' if len(policy.rule_text) > 100 else policy.rule_text,
                    'rule_type': policy.rule_type,
                    'severity': policy.severity,
                    'applies_to': policy.applies_to
                }
                for policy in policies
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{policy_id}", dependencies=[Depends(requires_permission(Permission.MANAGE_POLICIES))])
async def delete_policy(policy_id: str, tenant_id: str):
    """Soft delete policy."""
    try:
        repository = PolicyRepository()
        success = repository.delete_policy(policy_id, tenant_id)

        if not success:
            raise HTTPException(status_code=404, detail="Policy not found")
        
        return {
            'success': True,
            'message': f'Policy {policy_id} deleted successfully'
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
async def get_policy_capabilities():
    """Get policy management capabilities."""
    return {
        'capabilities': {
            'document_processing': 'Upload and process 50+ page policy documents',
            'rule_extraction': 'Extract mandatory, recommended, and prohibited rules',
            'semantic_search': 'Search policies using natural language queries',
            'compliance_checking': 'Check contract compliance against policies',
            'multi_tenancy': 'Tenant-isolated policy management',
            'versioning': 'Policy version control and history tracking'
        },
        'supported_formats': ['PDF', 'TXT', 'DOCX'],
        'rule_types': ['mandatory', 'recommended', 'prohibited', 'general'],
        'severity_levels': ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'],
        'contract_types': [
            'liability', 'termination', 'payment', 'confidentiality',
            'intellectual_property', 'data_protection', 'general'
        ]
    }