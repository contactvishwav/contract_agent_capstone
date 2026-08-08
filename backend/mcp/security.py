"""MCP identity binding and standalone-server trust policy."""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class McpPrincipal:
    tenant_id: str
    source: str


principal_var: ContextVar[Optional[McpPrincipal]] = ContextVar(
    "mcp_principal", default=None
)


class McpAuthorizationError(RuntimeError):
    pass


def resolve_tool_tenant(asserted_tenant_id: Optional[str]) -> tuple[str, str]:
    """Resolve tenant from a server-bound principal or trusted local mode."""
    principal = principal_var.get()
    if principal is not None:
        if asserted_tenant_id and asserted_tenant_id != principal.tenant_id:
            raise McpAuthorizationError("tenant identity mismatch")
        return principal.tenant_id, principal.source

    if os.getenv("ENVIRONMENT") == "production":
        raise McpAuthorizationError("authenticated MCP principal required")
    if not asserted_tenant_id:
        raise McpAuthorizationError("missing tenant identity")

    # Direct tool calls without a bound principal are supported only for
    # local/test execution.  They are not authenticated and must never be
    # presented as an externally secure production boundary.
    return asserted_tenant_id, "trusted_local_assertion"


def assert_standalone_server_allowed() -> None:
    """Require explicit local-only opt-in; production always fails closed."""
    environment = os.getenv("ENVIRONMENT")
    local_opt_in = os.getenv("MCP_STANDALONE_LOCAL_ONLY", "false").lower() == "true"
    if environment not in {"development", "test"} or not local_opt_in:
        raise RuntimeError(
            "Standalone MCP has no external principal-to-tenant authentication. "
            "It is disabled unless ENVIRONMENT is development/test and "
            "MCP_STANDALONE_LOCAL_ONLY=true."
        )
