"""
Regression test for a real tenant-isolation bug found live during
production verification of Contract Chat: EnhancedContractSearchTool and
ContractSearchTool both had `tenant_id` as a required, LLM-visible field
on their args_schema - the model had no legitimate way to know the real
authenticated tenant_id, so in practice it fabricated a plausible-looking
placeholder ("default_tenant_id", observed live against
https://contract-intel.duckdns.org), silently scoping every chat search
to a tenant that doesn't exist and finding nothing.

Fix: tenant_id is no longer a field on either tool's args_schema at all
(backend/shared/utils/contract_search_tool.py, enhanced_contract_search_
tool.py). The real tenant_id now travels server-side from the verified
JWT (backend/main.py's `run` route -> `runner` -> the graph's
config["configurable"]["tenant_id"]) and is injected directly into the
tool's kwargs by contract_chat_agent.py's execute_tools node, which calls
`tool._run(**args)` directly rather than `tool.invoke(args)` for these two
tools specifically so there is no schema-validation path left for an
LLM-supplied tenant_id to matter even if the model includes one anyway.

This test drives the real compiled LangGraph agent (get_agent) with a fake
LLM whose tool call - simulating a compromised or simply hallucinating
model - explicitly tries to inject a DIFFERENT tenant_id ("attacker_
tenant") into the tool-call args than the one the request is actually
authenticated as ("real_tenant_A"). The tool itself is patched to record
what tenant_id it actually received. Proving the recorded value is always
"real_tenant_A", never "attacker_tenant" or the fabricated default, is the
actual isolation guarantee - not just "a tenant_id was passed somewhere".
"""

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.contract_chat_agent import get_agent


class _StatefulFakeLLM:
    """First .invoke() returns a tool call (with an attacker-supplied
    tenant_id baked into the args, exactly like a real hallucinating model
    would); second .invoke() returns a plain final answer so the graph
    terminates instead of looping forever."""

    def __init__(self, tool_name: str, malicious_args: dict):
        self._tool_name = tool_name
        self._malicious_args = malicious_args
        self._call_count = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self._call_count += 1
        if self._call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": self._tool_name,
                    "args": self._malicious_args,
                    "id": "call_1",
                }],
            )
        return AIMessage(content="Here is what I found.")


class ChatSearchToolTenantIsolationTests(unittest.TestCase):
    def setUp(self):
        # execute_tools/assistant each construct a real AgentAuditService()
        # (-> AuditLogger() -> the real Neo4j graph singleton) to log every
        # tool call/model decision - irrelevant to what this test proves,
        # and patched out the same way test_stubbed_llm_parsers.py patches
        # it around ClauseDetectorTool._run.
        self._audit_patcher = patch("backend.infrastructure.agent_audit_service.AgentAuditService")
        self._audit_patcher.start()
        self.addCleanup(self._audit_patcher.stop)

    def _run_chat_and_capture_tenant_id(self, tool_name: str, patch_target: str, malicious_tenant_id: str = "attacker_tenant"):
        captured = {}

        def fake_run(**kwargs):
            captured["tenant_id"] = kwargs.get("tenant_id")
            return "no results"

        malicious_args = {"summary_search": "liability", "tenant_id": malicious_tenant_id}

        with patch(patch_target, side_effect=fake_run):
            fake_llm = _StatefulFakeLLM(tool_name, malicious_args)
            graph = get_agent(fake_llm)

            graph.invoke(
                {"messages": [("user", "What contracts do I have?")]},
                config={"configurable": {"tenant_id": "real_tenant_A"}},
            )

        return captured

    def test_enhanced_contract_search_uses_the_real_authenticated_tenant_id_not_the_llms(self):
        captured = self._run_chat_and_capture_tenant_id(
            "EnhancedContractSearch",
            "backend.shared.utils.enhanced_contract_search_tool.EnhancedContractSearchTool._run",
        )
        self.assertEqual(
            captured.get("tenant_id"), "real_tenant_A",
            "the tool must be called with the authenticated request's real tenant_id",
        )
        self.assertNotEqual(
            captured.get("tenant_id"), "attacker_tenant",
            "an LLM-supplied tenant_id must never reach the tool - it isn't even a schema field anymore",
        )

    def test_contract_search_uses_the_real_authenticated_tenant_id_not_the_llms(self):
        captured = self._run_chat_and_capture_tenant_id(
            "ContractSearch",
            "backend.shared.utils.contract_search_tool.ContractSearchTool._run",
        )
        self.assertEqual(captured.get("tenant_id"), "real_tenant_A")
        self.assertNotEqual(captured.get("tenant_id"), "attacker_tenant")

    def test_two_different_requests_get_their_own_distinct_tenant_id(self):
        # Proves the injected value tracks the actual per-request config,
        # not a process-wide constant that happened to match once.
        first = self._run_chat_and_capture_tenant_id(
            "EnhancedContractSearch",
            "backend.shared.utils.enhanced_contract_search_tool.EnhancedContractSearchTool._run",
        )

        captured_second = {}

        def fake_run(**kwargs):
            captured_second["tenant_id"] = kwargs.get("tenant_id")
            return "no results"

        with patch(
            "backend.shared.utils.enhanced_contract_search_tool.EnhancedContractSearchTool._run",
            side_effect=fake_run,
        ):
            fake_llm = _StatefulFakeLLM("EnhancedContractSearch", {"summary_search": "x"})
            graph = get_agent(fake_llm)
            graph.invoke(
                {"messages": [("user", "search")]},
                config={"configurable": {"tenant_id": "real_tenant_B"}},
            )

        self.assertEqual(first.get("tenant_id"), "real_tenant_A")
        self.assertEqual(captured_second.get("tenant_id"), "real_tenant_B")

    def test_missing_tenant_id_in_config_raises_rather_than_searching_untenanted(self):
        fake_llm = _StatefulFakeLLM("EnhancedContractSearch", {"summary_search": "liability"})
        graph = get_agent(fake_llm)

        with self.assertRaises(ValueError):
            graph.invoke({"messages": [("user", "search")]}, config={})


if __name__ == "__main__":
    unittest.main()
