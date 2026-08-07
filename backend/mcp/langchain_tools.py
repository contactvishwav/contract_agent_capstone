"""LangChain tool wrappers around the real MCP tools (backend/mcp_server.py),
invoked in-process via backend.mcp.client_bridge - see that module's
docstring for why an in-process client exists at all.

Contract Chat's existing 2 tools (ContractSearchTool, EnhancedContractSearchTool)
do contract/clause vector search. These 4 wrap genuinely different
capabilities Contract Chat didn't have before: policy/playbook clause
library search, playbook rule lookup, precedent-clause search, and
contract-metadata fetch - real, separate business logic
(PolicyRepository, EnhancedPrecedentMatcherTool, Neo4jContractRepository),
not a second path to what the existing 2 tools already do.

Each wrapper is deliberately thin - no business logic here, just
tenant_id/correlation_id threading and the shape LangChain expects.
tenant_id and correlation_id are NOT fields on any args_schema below: both
are injected server-side by contract_chat_agent.py's execute_tools (from
the verified JWT and the HTTP request's own correlation_id_var,
respectively), exactly matching how tenant_id is already kept out of
ContractInput/EnhancedContractInput. The LLM never sees or supplies either.
"""
import json
from typing import Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.mcp.client_bridge import call_mcp_tool, call_mcp_tool_sync


class _McpBridgeTool(BaseTool):
    """Shared _run/_arun for all MCP-backed chat tools: call the real MCP
    tool in-process with the server-injected tenant_id/correlation_id, and
    return its JSON payload as a string (matching the existing tools'
    convention of returning JSON-serialized results)."""

    mcp_tool_name: str

    def _run(self, tenant_id: str, correlation_id: Optional[str] = None, **kwargs) -> str:
        payload = call_mcp_tool_sync(
            self.mcp_tool_name,
            {**kwargs, "tenant_id": tenant_id},
            correlation_id=correlation_id,
        )
        return json.dumps(payload)

    async def _arun(self, tenant_id: str, correlation_id: Optional[str] = None, **kwargs) -> str:
        payload = await call_mcp_tool(
            self.mcp_tool_name,
            {**kwargs, "tenant_id": tenant_id},
            correlation_id=correlation_id,
        )
        return json.dumps(payload)


class ClauseLibraryInput(BaseModel):
    query: str = Field(..., description="Semantic search query for the clause library.")


class ClauseLibrarySearchTool(_McpBridgeTool):
    name: str = "ClauseLibrarySearch"
    description: str = (
        "Search the centralized clause library for standard legal language matching a query. "
        "Use this to find approved clause wording for a given topic, not to search a specific "
        "contract's own text - use ContractSearch/EnhancedContractSearch for that."
    )
    args_schema: Type[BaseModel] = ClauseLibraryInput
    mcp_tool_name: str = "search_clause_library"


class PlaybookRuleInput(BaseModel):
    contract_type: str = Field(
        "general", description="The type of contract, e.g. 'MSA', 'SOW', 'NDA', or 'general'."
    )


class PlaybookRuleLookupTool(_McpBridgeTool):
    name: str = "PlaybookRuleLookup"
    description: str = (
        "Retrieve the applicable legal playbook rules and corporate policies for a specific contract type."
    )
    args_schema: Type[BaseModel] = PlaybookRuleInput
    mcp_tool_name: str = "get_playbook_rule"


class PriorApprovedClauseInput(BaseModel):
    clause_text: str = Field(..., description="The content of the clause to find precedents for.")


class PriorApprovedClauseSearchTool(_McpBridgeTool):
    name: str = "PriorApprovedClauseSearch"
    description: str = (
        "Search historical records for previously approved clauses matching the given clause text. "
        "Useful for finding standard language and approval rates for similar wording."
    )
    args_schema: Type[BaseModel] = PriorApprovedClauseInput
    mcp_tool_name: str = "search_prior_approved_clauses"


class ContractMetadataInput(BaseModel):
    contract_id: str = Field(..., description="The unique identifier for the contract, e.g. 'UPLOADED_XXXX'.")


class ContractMetadataFetchTool(_McpBridgeTool):
    name: str = "ContractMetadataFetch"
    description: str = (
        "Fetch structured metadata (parties, dates, type, etc.) for a specific contract by its ID."
    )
    args_schema: Type[BaseModel] = ContractMetadataInput
    mcp_tool_name: str = "fetch_contract_metadata"


MCP_CHAT_TOOLS = [
    ClauseLibrarySearchTool(),
    PlaybookRuleLookupTool(),
    PriorApprovedClauseSearchTool(),
    ContractMetadataFetchTool(),
]

MCP_CHAT_TOOL_NAMES = {t.name for t in MCP_CHAT_TOOLS}
