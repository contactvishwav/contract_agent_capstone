import json
import logging
from typing import Any, Dict, List, Optional
from fastmcp import FastMCP

from backend.infrastructure.policy_repository import PolicyRepository
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.agents.enhanced_cuad_tools import EnhancedPrecedentMatcherTool
from backend.mcp.decorators import mcp_tool_wrapper
from backend.shared.utils.mcp_logger import get_mcp_logger

# Initialize FastMCP server
mcp = FastMCP("ContractIntelligence")
logger = get_mcp_logger()

# Repositories (SOLID: Lazy loading to avoid side-effects on import)
_policy_repo = None
_contract_repo = None
_precedent_matcher = None

def get_policy_repo():
    global _policy_repo
    if _policy_repo is None:
        _policy_repo = PolicyRepository()
    return _policy_repo

def get_contract_repo():
    global _contract_repo
    if _contract_repo is None:
        _contract_repo = Neo4jContractRepository()
    return _contract_repo

def get_precedent_matcher():
    global _precedent_matcher
    if _precedent_matcher is None:
        _precedent_matcher = EnhancedPrecedentMatcherTool()
    return _precedent_matcher

@mcp.tool()
@mcp_tool_wrapper
async def search_clause_library(query: str, tenant_id: str, correlation_id: Optional[str] = None) -> str:
    """
    Search the centralized clause library for legal language matching the query.

    Args:
        query: Semantic search query for the clause library.
        tenant_id: Mandatory tenant identifier for data isolation.
        correlation_id: Optional caller-supplied id (e.g. the originating HTTP
            request's X-Correlation-ID) to link this call's logs/audit trail
            back to that request. Omit to get a fresh, unrelated trace id.
    """
    try:
        results = get_policy_repo().search_policies_semantic(query, tenant_id)
        return json.dumps({
            "success": True,
            "results_count": len(results),
            "clauses": results
        })
    except Exception:
        return json.dumps({"success": False, "error": "Clause library search failed"})

@mcp.tool()
@mcp_tool_wrapper
async def get_playbook_rule(tenant_id: str, contract_type: str = "general", correlation_id: Optional[str] = None) -> str:
    """
    Retrieve applicable legal playbook rules and corporate policies for a specific contract type.

    Looks up organization-wide compliance rules filed under a generic
    contract_type category (e.g. 'MSA', 'SOW', 'general') - NOT the content
    of any specific uploaded contract or document. Do not call this to
    answer a question about what a particular selected document says, even
    if that document's own filename or content is itself named or described
    as a "policy playbook" - that is a naming coincidence, not a match. This
    tool cannot see or search a document's real text at all; use
    EnhancedContractSearch for that instead.

    Args:
        tenant_id: Mandatory tenant identifier for data isolation.
        contract_type: The type of contract (e.g., 'MSA', 'SOW', 'NDA', 'general').
        correlation_id: Optional caller-supplied id (e.g. the originating HTTP
            request's X-Correlation-ID) to link this call's logs/audit trail
            back to that request. Omit to get a fresh, unrelated trace id.
    """
    try:
        rules = get_policy_repo().get_applicable_policies(tenant_id, contract_type)
        return json.dumps({
            "success": True,
            "contract_type": contract_type,
            "rules_count": len(rules),
            "rules": [
                {
                    "id": r.id,
                    "text": r.rule_text,
                    "severity": r.severity,
                    "type": r.rule_type
                } for r in rules
            ]
        })
    except Exception:
        return json.dumps({"success": False, "error": "Playbook lookup failed"})

@mcp.tool()
@mcp_tool_wrapper
async def search_prior_approved_clauses(clause_text: str, tenant_id: str, correlation_id: Optional[str] = None) -> str:
    """
    Search historical metadata for previously approved clauses that match the provided text.
    Helpful for finding standard language and approval rates.

    Args:
        clause_text: The content of the clause to find precedents for.
        tenant_id: Mandatory tenant identifier for data isolation.
        correlation_id: Optional caller-supplied id (e.g. the originating HTTP
            request's X-Correlation-ID) to link this call's logs/audit trail
            back to that request. Omit to get a fresh, unrelated trace id.
    """
    try:
        clauses_input = json.dumps([{"clause_type": "unknown", "content": clause_text}])
        results_json = get_precedent_matcher()._run(clauses_input, tenant_id=tenant_id)
        results = json.loads(results_json)
        
        return json.dumps({
            "success": True,
            "tenant_id": tenant_id,
            "precedent_matches": results
        })
    except Exception:
        return json.dumps({"success": False, "error": "Precedent search failed"})

@mcp.tool()
@mcp_tool_wrapper
async def fetch_contract_metadata(contract_id: str, tenant_id: str, correlation_id: Optional[str] = None) -> str:
    """
    Retrieve structured metadata for a specific contract by its ID.

    Args:
        contract_id: The unique identifier for the contract (e.g., UPLOADED_XXXX).
        tenant_id: Mandatory tenant identifier for data isolation.
        correlation_id: Optional caller-supplied id (e.g. the originating HTTP
            request's X-Correlation-ID) to link this call's logs/audit trail
            back to that request. Omit to get a fresh, unrelated trace id.
    """
    try:
        metadata = await get_contract_repo().get_contract_by_id(contract_id, tenant_id=tenant_id)
        if not metadata:
            return json.dumps({"success": False, "error": "Contract not found"})
            
        return json.dumps({
            "success": True,
            "metadata": metadata
        })
    except Exception:
        return json.dumps({"success": False, "error": "Contract metadata lookup failed"})

if __name__ == "__main__":
    from backend.mcp.security import assert_standalone_server_allowed

    # The stdio client is the local OS principal and may assert tenant only
    # under explicit local-development opt-in. There is no external
    # principal mapping here, so production startup fails closed.
    assert_standalone_server_allowed()
    mcp.run()
