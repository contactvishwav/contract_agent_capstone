"""
Tests for backend/mcp/client_bridge.py - the new in-process FastAPI -> MCP
call path (docs/CAPSTONE_SUMMARY.md §4 item 15/§8: "nothing in this
codebase currently calls from FastAPI into the MCP server in-process").

These tests call the REAL fastmcp in-memory Client -> real FastMCP server
instance (backend.mcp_server.mcp) -> the real @mcp.tool()-decorated
functions -> the real mcp_tool_wrapper decorator, over the real MCP
protocol - only the bottom-most repository call is patched (same
patch points test_mcp_capabilities.py already uses), so this is a
genuine exercise of the new call path, not a reimplementation of it.

Deliberately NOT unittest.IsolatedAsyncioTestCase: that gives each test
method its own fresh asyncio event loop while backend.mcp_server.mcp (the
FastMCP server instance these tests call into) is a shared module-level
singleton - reusing it from a second, different IsolatedAsyncioTestCase
loop was found live to spin fastmcp's in-memory session machinery into a
runaway loop (see the investigation that led to this file's current
shape). The REAL production call path (call_mcp_tool_sync -> a fresh
asyncio.run() per call, see client_bridge.py) was verified live to NOT
have this problem across repeated sequential calls - asyncio.run() creates
and fully tears down its loop each time, unlike IsolatedAsyncioTestCase's
longer-lived per-test loop. So these tests call asyncio.run() directly,
matching the real, proven-safe call pattern instead of the pattern that
broke.
"""
import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.mcp.client_bridge import call_mcp_tool, call_mcp_tool_sync
    from backend.shared.utils.mcp_logger import trace_id_var

# The installed mcp SDK's own low-level server (mcp.server.lowlevel.server)
# emits a DeprecationWarning (datetime.datetime.utcnow()) from inside a
# session-management loop hit on every in-memory tool call. Under plain
# Python this is deduplicated (shown once) by the default warnings filter
# and is harmless background noise from a third-party dependency, not
# something this codebase's tests should chase. Under pytest specifically,
# the warnings plugin resets the filter to "always" per test, so the same
# loop logs it on every iteration instead of once - found live to turn a
# ~1s real call into an unbounded hang. Suppressing it here restores the
# real (fast, bounded) behavior these tests are actually verifying.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class CallMcpToolSuccessTests(unittest.TestCase):
    def setUp(self):
        trace_id_var.set(None)

    @patch("backend.mcp_server.get_policy_repo")
    def test_real_in_process_call_reaches_the_real_tool_function(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_applicable_policies.return_value = []

        payload = asyncio.run(call_mcp_tool(
            "get_playbook_rule",
            {"tenant_id": "tenant_bridge_1", "contract_type": "NDA"},
            authenticated_tenant_id="tenant_bridge_1",
        ))

        self.assertTrue(payload["success"])
        self.assertEqual(payload["contract_type"], "NDA")
        mock_repo.get_applicable_policies.assert_called_once_with("tenant_bridge_1", "NDA")

    @patch("backend.mcp_server.get_policy_repo")
    def test_correlation_id_genuinely_propagates_to_the_real_tool_and_real_logs(self, mock_get_repo):
        """The core proof this feature needs to exist at all: an id chosen
        by the caller (standing in for the HTTP side's X-Correlation-ID)
        must reach the real @mcp.tool()-wrapped function's execution and
        show up in its real log output - not just be accepted as an
        argument."""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.search_policies_semantic.return_value = [{"id": "c1", "text": "sample"}]

        logger = logging.getLogger("mcp_server")
        with self.assertLogs(logger, level="INFO") as captured:
            payload = asyncio.run(call_mcp_tool(
                "search_clause_library",
                {"tenant_id": "tenant_bridge_2", "query": "liability cap"},
                correlation_id="corr-live-e2e-42",
                authenticated_tenant_id="tenant_bridge_2",
            ))

        self.assertTrue(payload["success"])
        # mcp_tool_wrapper seeds trace_id_var from the passed correlation_id
        # (backend/mcp/decorators.py:24) and every log call in that
        # execution carries it via `extra={"trace_id": tid}"` - so the real
        # log record for this real tool execution must show it.
        matching = [r for r in captured.records if getattr(r, "trace_id", None) == "corr-live-e2e-42"]
        self.assertTrue(
            matching,
            f"expected at least one real log record tagged with the passed correlation_id; got trace_ids: "
            f"{[getattr(r, 'trace_id', None) for r in captured.records]}",
        )
        self.assertTrue(any("search_clause_library" in r.getMessage() for r in matching))

    @patch("backend.mcp_server.get_contract_repo")
    def test_omitted_correlation_id_still_gets_a_fresh_trace_id(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_contract_by_id = AsyncMock(return_value={"file_id": "C1"})

        payload = asyncio.run(call_mcp_tool(
            "fetch_contract_metadata",
            {"tenant_id": "tenant_bridge_3", "contract_id": "C1"},
            authenticated_tenant_id="tenant_bridge_3",
        ))
        self.assertTrue(payload["success"])


class CallMcpToolGracefulFailureTests(unittest.TestCase):
    """If the MCP server is unavailable (here: the in-memory Client itself
    fails to connect/call, simulating a broken server-side dependency the
    tool's own try/except didn't catch), the bridge must degrade to a
    structured error rather than raise - so a Contract Chat turn can
    report "that lookup failed" instead of crashing entirely."""

    def test_client_connection_failure_degrades_gracefully_not_raises(self):
        with patch("backend.mcp.client_bridge.Client", side_effect=RuntimeError("mcp session unavailable")):
            payload = asyncio.run(call_mcp_tool(
                "get_playbook_rule",
                {"tenant_id": "tenant_bridge_4", "contract_type": "NDA"},
                authenticated_tenant_id="tenant_bridge_4",
            ))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "MCP tool call unavailable")

    def test_unknown_tool_name_degrades_gracefully_not_raises(self):
        payload = asyncio.run(call_mcp_tool(
            "this_tool_does_not_exist", {"tenant_id": "t1"},
            authenticated_tenant_id="t1",
        ))
        self.assertFalse(payload.get("success", False))
        self.assertIn("error", payload)


class CallMcpToolSyncTests(unittest.TestCase):
    """call_mcp_tool_sync is what the LangChain tools' _run actually calls
    (see contract_chat_agent.py's execute_tools, which calls tool._run(...)
    synchronously) - must work both with and without an already-running
    event loop in the calling thread, and must work across repeated calls
    (the real Contract Chat usage pattern: one call per tool invocation,
    potentially many per process lifetime)."""

    @patch("backend.mcp_server.get_policy_repo")
    def test_sync_call_with_no_running_loop(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_applicable_policies.return_value = []

        payload = call_mcp_tool_sync(
            "get_playbook_rule",
            {"tenant_id": "tenant_sync_1", "contract_type": "MSA"},
            authenticated_tenant_id="tenant_sync_1",
        )
        self.assertTrue(payload["success"])

    @patch("backend.mcp_server.get_policy_repo")
    def test_repeated_sequential_sync_calls_do_not_hang_or_degrade(self, mock_get_repo):
        """Regression guard for the exact failure mode found live during
        development: reusing the shared mcp_server singleton across
        multiple asyncio.run() calls must stay reliable - this is the real
        pattern every Contract Chat MCP tool call goes through
        (call_mcp_tool_sync -> a fresh asyncio.run() per call)."""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_applicable_policies.return_value = []

        for i in range(3):
            payload = call_mcp_tool_sync(
                "get_playbook_rule",
                {"tenant_id": f"tenant_repeat_{i}", "contract_type": "NDA"},
                authenticated_tenant_id=f"tenant_repeat_{i}",
            )
            self.assertTrue(payload["success"], f"call {i} failed: {payload}")

    @patch("backend.mcp_server.get_policy_repo")
    def test_sync_call_from_inside_a_running_loop_does_not_raise(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_applicable_policies.return_value = []

        async def call_from_within_a_loop():
            # Simulates a sync callee (call_mcp_tool_sync) invoked from
            # code that itself is already running inside an event loop -
            # asyncio.run() nested directly here would raise
            # "cannot be called from a running event loop"; the sync
            # wrapper must route around that instead of propagating it.
            return call_mcp_tool_sync(
                "get_playbook_rule",
                {"tenant_id": "tenant_sync_2", "contract_type": "MSA"},
                authenticated_tenant_id="tenant_sync_2",
            )

        payload = asyncio.run(call_from_within_a_loop())
        self.assertTrue(payload["success"])


if __name__ == "__main__":
    unittest.main()
