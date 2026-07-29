"""
Regression tests for item 15 in docs/ENTERPRISE_READINESS.md's punch list:
trace_id_var (MCP side) and correlation_id_var (HTTP side) were two fully
disconnected ContextVars - the MCP server runs as a separate stdio process,
so a request crossing HTTP -> MCP ended up with two unrelated ids in its
logs. Since nothing in this codebase currently calls from the FastAPI app
into the MCP server (it's only invoked externally, e.g. by an MCP client),
bridging means the MCP side must be able to ACCEPT a caller-supplied
correlation_id and prefer it over generating a fresh, disconnected trace_id
- not an automatic end-to-end wire-up, since no such call path exists yet.
"""

import asyncio
import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.mcp.decorators import mcp_tool_wrapper
    from backend.shared.utils.mcp_logger import trace_id_var


class CorrelationIdBridgeTests(unittest.TestCase):
    def setUp(self):
        trace_id_var.set(None)

    def test_passed_correlation_id_seeds_trace_id(self):
        @mcp_tool_wrapper
        async def fake_tool(tenant_id: str, correlation_id=None) -> str:
            return trace_id_var.get()

        result = asyncio.run(fake_tool(tenant_id="tenant_1", correlation_id="http-request-abc123"))

        self.assertEqual(result, "http-request-abc123")

    def test_omitted_correlation_id_falls_back_to_fresh_trace_id(self):
        @mcp_tool_wrapper
        async def fake_tool(tenant_id: str, correlation_id=None) -> str:
            return trace_id_var.get()

        result = asyncio.run(fake_tool(tenant_id="tenant_1"))

        self.assertIsNotNone(result)
        self.assertNotEqual(result, "http-request-abc123")

    def test_passed_correlation_id_overrides_existing_trace_id_var(self):
        trace_id_var.set("some-stale-trace-id")

        @mcp_tool_wrapper
        async def fake_tool(tenant_id: str, correlation_id=None) -> str:
            return trace_id_var.get()

        result = asyncio.run(fake_tool(tenant_id="tenant_1", correlation_id="http-request-xyz"))

        self.assertEqual(result, "http-request-xyz")

    def test_two_calls_with_same_correlation_id_share_trace_id(self):
        seen = []

        @mcp_tool_wrapper
        async def fake_tool(tenant_id: str, correlation_id=None) -> str:
            seen.append(trace_id_var.get())
            return "ok"

        asyncio.run(fake_tool(tenant_id="t1", correlation_id="shared-corr-id"))
        asyncio.run(fake_tool(tenant_id="t1", correlation_id="shared-corr-id"))

        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[0], "shared-corr-id")


class McpServerToolSignaturesTests(unittest.TestCase):
    """
    Confirms all 4 real MCP tool functions accept correlation_id as an
    optional (backward-compatible) parameter.

    Inspects mcp_server.py's source via `ast` rather than importing the
    module: `fastmcp.FastMCP`'s lazy __getattr__ import needs `mcp.types`,
    which this venv's fastmcp install doesn't have (the same pre-existing
    gap that already excludes test_mcp_capabilities.py from the standard
    full-suite run) - importing here would make this test's pass/fail
    depend on unrelated import-order/environment state rather than the
    actual thing being tested (the function signatures).
    """

    TOOL_NAMES = {
        "search_clause_library", "get_playbook_rule",
        "search_prior_approved_clauses", "fetch_contract_metadata",
    }

    def test_all_four_tools_accept_optional_correlation_id(self):
        import ast
        import os

        mcp_server_path = os.path.join(
            os.path.dirname(__file__), "..", "mcp_server.py"
        )
        with open(mcp_server_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=mcp_server_path)

        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in self.TOOL_NAMES:
                found.add(node.name)
                arg_names = [a.arg for a in node.args.args]
                self.assertIn("correlation_id", arg_names, f"{node.name} missing correlation_id param")

                # Match the default (last N args line up with last N defaults)
                defaults_by_arg = dict(zip(arg_names[-len(node.args.defaults):], node.args.defaults))
                default_node = defaults_by_arg.get("correlation_id")
                self.assertIsInstance(
                    default_node, ast.Constant,
                    f"{node.name}'s correlation_id must have a literal default",
                )
                self.assertIsNone(
                    default_node.value,
                    f"{node.name}'s correlation_id must default to None (backward-compatible)",
                )

        self.assertEqual(found, self.TOOL_NAMES, "Not all 4 MCP tool functions were found in mcp_server.py")


if __name__ == "__main__":
    unittest.main()
