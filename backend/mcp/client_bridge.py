"""In-process FastAPI -> MCP call path.

Before this module, backend/mcp_server.py's 4 real MCP tools were only
reachable by an external MCP client speaking to the standalone
`python backend/mcp_server.py` stdio process - nothing in this codebase
called from FastAPI into the MCP server, so a correlation_id generated on
the HTTP side had no way to reach a real MCP tool invocation.

This uses fastmcp's in-memory transport (`fastmcp.Client(mcp_server)`,
which auto-detects a live `FastMCP` instance via
`fastmcp.client.transports.inference.infer_transport` and wires it
directly to the real MCP protocol over anyio in-memory streams - no
subprocess, no socket) to call the exact same tool functions, in the same
process, over the real MCP protocol. The standalone stdio process remains
the entry point for external MCP clients and is untouched.
"""
import asyncio
import concurrent.futures
import json
import logging
from typing import Any, Dict, Optional

from fastmcp import Client

from backend.mcp_server import mcp as mcp_server
from backend.mcp.security import McpPrincipal, principal_var

logger = logging.getLogger(__name__)


async def call_mcp_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    correlation_id: Optional[str] = None,
    authenticated_tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Call a real MCP tool in-process and return its parsed JSON payload.

    Never raises: a transport-level failure (tool not found, a bug in
    fastmcp's in-memory session) or any tool-layer exception the tool
    itself didn't already catch degrades to a structured error dict, so a
    calling chat turn can continue instead of crashing - the same
    graceful-degradation convention used for reranking (reranker_service.py)
    and every other external-dependency call in this codebase.
    """
    if not authenticated_tenant_id:
        return {"success": False, "error": "Authenticated MCP tenant is required"}

    # The bridge is a server-side boundary: discard any tenant assertion in
    # the generic argument bag and inject the JWT-derived value explicitly.
    call_args = {key: value for key, value in arguments.items() if key != "tenant_id"}
    call_args["tenant_id"] = authenticated_tenant_id
    if correlation_id:
        call_args["correlation_id"] = correlation_id

    token = principal_var.set(McpPrincipal(authenticated_tenant_id, "authenticated_in_process"))
    try:
        async with Client(mcp_server) as client:
            result = await client.call_tool(tool_name, call_args, raise_on_error=False)
    except Exception as e:
        logger.error(
            "MCP in-process call failed for tool '%s': %s",
            tool_name,
            type(e).__name__,
        )
        return {"success": False, "error": "MCP tool call unavailable"}
    finally:
        principal_var.reset(token)

    raw = result.data
    if raw is None and result.content:
        raw = "".join(
            block.text for block in result.content if hasattr(block, "text")
        )

    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # Not one of the 4 tools' own JSON-error responses (those are
            # always valid JSON) - e.g. an unknown tool name, or a raw MCP
            # protocol-level error string. Normalize to the same
            # {"success", "error"} shape every real tool already uses, so
            # callers only need to check one key regardless of which layer
            # failed.
            payload = {"success": not result.is_error, "error": raw}
    else:
        payload = {"success": not result.is_error, "error": str(raw)}

    if result.is_error and "success" not in payload:
        payload["success"] = False
    return payload


def call_mcp_tool_sync(
    tool_name: str,
    arguments: Dict[str, Any],
    correlation_id: Optional[str] = None,
    authenticated_tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sync entry point for call sites that can't await directly (LangChain
    tools' `_run` - see contract_chat_agent.py's execute_tools, which calls
    tool._run(**args) synchronously).

    Safe whether or not the current thread already has a running event
    loop. The expected case is no running loop (LangGraph executes sync
    nodes in a worker thread), so this runs the coroutine directly via
    asyncio.run(). If a loop IS already running in the current thread (e.g.
    a test that awaits an async caller which then invokes a tool's sync
    _run), asyncio.run() would raise "cannot be called from a running event
    loop" - so instead the coroutine runs to completion in a separate
    thread with its own fresh loop, and this call blocks on that thread's
    result rather than nesting event loops.
    """
    coro = call_mcp_tool(
        tool_name,
        arguments,
        correlation_id,
        authenticated_tenant_id=authenticated_tenant_id,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
