import functools
import uuid
import asyncio
import json
from typing import Any, Callable, Dict, Optional
from backend.shared.utils.mcp_logger import get_mcp_logger, trace_id_var
from backend.mcp.security import McpAuthorizationError, resolve_tool_tenant

logger = get_mcp_logger()

def mcp_tool_wrapper(func: Callable) -> Callable:
    """
    SOLID & DRY: Centralized decorator for MCP tools to handle tracing, 
    logging, and tenant validation.
    """
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs) -> Any:
        # 1. Initialize Trace ID - prefer an explicitly-passed correlation_id
        # (threaded through by a caller that already has the HTTP side's
        # X-Correlation-ID) over generating a fresh, disconnected one. The
        # MCP server runs as a separate stdio process with no other
        # mechanism linking its logs back to the originating HTTP request,
        # so this is what lets one identifier span both sides when a caller
        # has it to give.
        tid = kwargs.get("correlation_id") or trace_id_var.get()
        if not tid:
            tid = str(uuid.uuid4())
        trace_id_var.set(tid)
            
        # 2. Validate tenant_id (Mandatory requirement per implementation plan)
        asserted_tenant_id = kwargs.get("tenant_id")
        try:
            tenant_id, identity_source = resolve_tool_tenant(asserted_tenant_id)
        except McpAuthorizationError as exc:
            logger.error(
                f"MCP authorization failed for tool {func.__name__}",
                exc,
                metadata={"tool_name": func.__name__, "trace_id": tid},
            )
            message = (
                "Missing mandatory 'tenant_id' parameter"
                if str(exc) == "missing tenant identity"
                else "Authenticated MCP tenant is required"
            )
            return json.dumps({"error": message, "status": "failed", "trace_id": tid})

        kwargs["tenant_id"] = tenant_id

        # 3. Log execution
        logger.log_tool_execution(
            func.__name__, tenant_id,
            metadata={
                "argument_names": sorted(kwargs.keys()),
                "identity_source": identity_source,
            },
        )
        
        try:
            # 4. Execute the tool
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
                
            logger.info(f"Successfully completed {func.__name__} for tenant {tenant_id}")
            return result
        except Exception as e:
            logger.error(
                f"Error in tool {func.__name__} for tenant {tenant_id}",
                e,
                tenant_id=tenant_id,
            )
            return json.dumps({"error": "MCP tool execution failed", "status": "failed", "trace_id": tid})
            
    return async_wrapper
