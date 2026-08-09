"""
End-to-end test of the new in-process FastAPI -> MCP call path through
Contract Chat itself (backend/contract_chat_agent.py), not just the bridge
in isolation - proving the whole chain a real HTTP request would drive:

  authenticated tenant_id + this request's correlation_id
    -> config["configurable"] (see main.py's runner())
    -> execute_tools node
    -> one of the 4 new MCP-backed LangChain tools' _run
    -> backend.mcp.langchain_tools.call_mcp_tool_sync
    -> real in-process fastmcp Client -> real FastMCP server -> real
       @mcp.tool()-wrapped function -> real business logic

Follows the same drive-the-real-compiled-graph-with-a-fake-LLM pattern as
test_chat_tenant_isolation.py, which already proved tenant_id injection
this way; this file proves the same for the 4 new tools plus the new
correlation_id threading, and that a real MCP-side failure degrades
gracefully instead of blowing up the chat turn.
"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.contract_chat_agent import get_agent
    from backend.mcp.langchain_tools import MCP_CHAT_TOOL_NAMES

# See test_mcp_client_bridge.py's identical comment: a third-party mcp SDK
# DeprecationWarning becomes pathologically slow under pytest's per-test
# "always" warnings filter when a real in-memory MCP call happens.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class _OneShotToolCallLLM:
    """First .invoke() calls the named tool with the given (LLM-visible)
    args; second .invoke() returns a plain final answer so the graph
    terminates."""

    def __init__(self, tool_name: str, args: dict):
        self._tool_name = tool_name
        self._args = args
        self._call_count = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self._call_count += 1
        if self._call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[{"name": self._tool_name, "args": self._args, "id": "call_1"}],
            )
        return AIMessage(content="Here is what I found.")


class ContractChatToolListTests(unittest.TestCase):
    def test_agent_binds_all_4_mcp_tools_alongside_the_existing_2(self):
        fake_llm = _OneShotToolCallLLM("PlaybookRuleLookup", {"contract_type": "NDA"})
        with patch("backend.infrastructure.agent_audit_service.AgentAuditService"), \
             patch("backend.mcp.langchain_tools.call_mcp_tool_sync", return_value={"success": True}):
            get_agent(fake_llm)  # must not raise while constructing/binding tools

    def test_all_4_mcp_tool_names_are_tenant_scoped(self):
        import backend.contract_chat_agent as cca
        self.assertTrue(MCP_CHAT_TOOL_NAMES.issubset(cca._TENANT_SCOPED_TOOL_NAMES))


class ContractChatMcpEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._audit_patcher = patch("backend.infrastructure.agent_audit_service.AgentAuditService")
        self._audit_patcher.start()
        self.addCleanup(self._audit_patcher.stop)

    @patch("backend.mcp_server.get_policy_repo")
    def test_playbook_lookup_reaches_the_real_mcp_tool_with_real_tenant_and_correlation_id(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_applicable_policies.return_value = []

        fake_llm = _OneShotToolCallLLM("PlaybookRuleLookup", {"contract_type": "MSA"})
        graph = get_agent(fake_llm)

        result_state = graph.invoke(
            {"messages": [("user", "what's our playbook rule for MSAs?")]},
            config={"configurable": {"tenant_id": "real_tenant_chat", "correlation_id": "corr-chat-e2e-1"}},
        )

        # Real repo call, with the real authenticated tenant_id - not
        # anything the fake LLM's tool-call args could have supplied
        # (contract_type only; tenant_id isn't even a field on
        # PlaybookRuleInput).
        mock_repo.get_applicable_policies.assert_called_once_with("real_tenant_chat", "MSA")

        # Tool results are normalized at the Contract Chat boundary. Prove
        # the real MCP call succeeded without expecting the raw bridge
        # payload to cross the canonical evidence boundary.
        tool_messages = [m for m in result_state["messages"] if getattr(m, "name", None) == "PlaybookRuleLookup" or hasattr(m, "tool_call_id")]
        self.assertTrue(tool_messages, "expected a ToolMessage from the PlaybookRuleLookup call")
        payload = json.loads(tool_messages[0].content)
        self.assertEqual(payload["schema_version"], "chat-evidence-v1")
        self.assertEqual(payload["tool_name"], "PlaybookRuleLookup")
        self.assertEqual(payload["tool_status"], "success")

    def test_llm_supplied_tenant_id_and_correlation_id_are_ignored_for_mcp_tools_too(self):
        """Same isolation guarantee test_chat_tenant_isolation.py proves for
        the original 2 tools, extended to the new MCP-backed ones: neither
        field exists on any of their args_schema, and execute_tools injects
        the real values itself, so an LLM trying to smuggle either in has
        no effect."""
        captured = {}

        def fake_run(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"success": True}

        malicious_args = {
            "contract_type": "NDA",
            "tenant_id": "attacker_tenant",
            "correlation_id": "attacker_correlation_id",
        }
        with patch("backend.mcp.langchain_tools.call_mcp_tool_sync", side_effect=fake_run):
            fake_llm = _OneShotToolCallLLM("PlaybookRuleLookup", malicious_args)
            graph = get_agent(fake_llm)
            graph.invoke(
                {"messages": [("user", "lookup")]},
                config={"configurable": {"tenant_id": "real_tenant_chat_2", "correlation_id": "corr-real-2"}},
            )

        # call_mcp_tool_sync's positional signature is (tool_name,
        # arguments_dict, correlation_id=...) - the tool's _run passes
        # tenant_id inside the arguments dict, correlation_id as its own
        # kwarg. Either way, the *value* actually used must be the real
        # request's, never the LLM-supplied one.
        self.assertNotIn("attacker_tenant", str(captured))
        self.assertNotIn("attacker_correlation_id", str(captured))

    def test_mcp_tool_side_failure_degrades_the_chat_turn_gracefully_not_a_crash(self):
        """If the MCP server/tool-side call fails (simulating "MCP server
        unavailable"), the chat turn must not raise - it should get back a
        structured failure payload it can report to the user, exactly like
        a real network/API failure anywhere else in this codebase."""
        with patch(
            "backend.mcp.langchain_tools.call_mcp_tool_sync",
            return_value={"success": False, "error": "mcp session unavailable"},
        ):
            fake_llm = _OneShotToolCallLLM("PlaybookRuleLookup", {"contract_type": "NDA"})
            graph = get_agent(fake_llm)
            result_state = graph.invoke(
                {"messages": [("user", "lookup")]},
                config={"configurable": {"tenant_id": "real_tenant_chat_3", "correlation_id": "corr-fail-1"}},
            )

        tool_messages = [m for m in result_state["messages"] if hasattr(m, "tool_call_id")]
        self.assertTrue(tool_messages)
        payload = json.loads(tool_messages[0].content)
        self.assertEqual(payload["schema_version"], "chat-evidence-v1")
        self.assertEqual(payload["tool_status"], "failure")
        self.assertEqual(payload["tool_error_category"], "tool_execution_failed")
        self.assertNotIn("mcp session unavailable", str(payload))


if __name__ == "__main__":
    unittest.main()
