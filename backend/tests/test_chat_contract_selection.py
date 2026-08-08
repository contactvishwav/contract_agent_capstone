"""
Contract Chat functional audit, item 3: Contract Chat had no way to know
which contract "this"/"it" referred to in questions like "Analyze this
contract" or "Tell me about this contract" - there was no contract-
selection mechanism anywhere in the Chat UI, and the /api/run/ request
schema had no field for it at all, so the agent could only ever ask the
user for a contract ID they had no way to obtain.

Fixed by adding a contract selector to the Chat UI (frontend/src/
components/features/contracts/input.tsx), threading the selected
contract_id through RunPayload -> runner() -> config["configurable"] ->
contract_chat_agent.py's execute_tools -> EnhancedContractSearchTool.
_run's kwargs (server-injected only, same trust boundary as tenant_id -
never a field the LLM can see, guess, or override) -> get_contracts_
multi_level, which now threads it into every search level's Cypher
filter, including 'all' (the new default from item 1's fix, which
previously silently ignored contract-scoping filters passed to it).
"""

import unittest
from unittest.mock import MagicMock, patch


def _search_tool_module():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        from backend.shared.utils import enhanced_contract_search_tool
    return enhanced_contract_search_tool


class FakeGraph:
    def __init__(self):
        self.queries = []

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        return []


class ContractIdScopesEveryLevelTests(unittest.TestCase):
    """When a contract is selected, its file_id/contract_id must appear
    in the real Cypher filters/params for every level - proven per
    level, since 'all' (the default) builds its own filters per
    sub-call rather than reusing a shared one (a real, separate
    plumbing detail that's easy to silently miss for one level)."""

    def _fake_embeddings(self):
        fake = MagicMock()
        fake.embed_query.return_value = [0.1, 0.2, 0.3]
        return fake

    def test_document_level_filters_by_contract_id(self):
        tool_module = _search_tool_module()
        fake_graph = FakeGraph()
        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False), \
             patch.object(tool_module, "graph", fake_graph):
            tool_module.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_a",
                search_level=tool_module.SearchLevel.DOCUMENT,
                summary_search="fees", contract_id="UPLOADED_ABC123",
            )
        cypher, params = fake_graph.queries[0]
        self.assertIn("c.file_id = $contract_id", cypher)
        self.assertEqual(params.get("contract_id"), "UPLOADED_ABC123")

    def test_chunk_level_filters_by_contract_id(self):
        tool_module = _search_tool_module()
        fake_graph = FakeGraph()
        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False), \
             patch.object(tool_module, "graph", fake_graph):
            tool_module.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_a",
                search_level=tool_module.SearchLevel.CHUNK,
                summary_search="fees", contract_id="UPLOADED_ABC123",
            )
        cypher, params = fake_graph.queries[0]
        self.assertIn("d.contract_id = $contract_id", cypher)
        self.assertEqual(params.get("contract_id"), "UPLOADED_ABC123")

    def test_all_level_scopes_every_sub_call_including_chunks(self):
        """The real regression: 'all' builds its own filters/params per
        sub-call and previously had no way to receive contract_id at
        all - confirmed here across all 5 sub-queries it issues."""
        tool_module = _search_tool_module()
        fake_graph = FakeGraph()
        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False), \
             patch.object(tool_module, "graph", fake_graph):
            tool_module.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_a",
                search_level=tool_module.SearchLevel.ALL,
                summary_search="fees", contract_id="UPLOADED_ABC123",
            )

        # documents/sections/clauses/relationships all key off Contract.file_id
        contract_scoped = [
            (c, p) for c, p in fake_graph.queries
            if "c.file_id = $contract_id" in c or "d.contract_id = $contract_id" in c
        ]
        self.assertGreaterEqual(
            len(contract_scoped), 5,
            f"expected every one of the 5 levels to be contract-scoped, got: {[c for c, _ in fake_graph.queries]}"
        )
        for _, params in contract_scoped:
            self.assertEqual(params.get("contract_id"), "UPLOADED_ABC123")

    def test_no_contract_selected_searches_tenant_wide_as_before(self):
        """contract_id=None (the 'All contracts' UI option) must not
        change existing tenant-wide behavior at all."""
        tool_module = _search_tool_module()
        fake_graph = FakeGraph()
        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False), \
             patch.object(tool_module, "graph", fake_graph):
            tool_module.get_contracts_multi_level(
                self._fake_embeddings(), tenant_id="tenant_a",
                search_level=tool_module.SearchLevel.DOCUMENT,
                summary_search="fees",
            )
        cypher, params = fake_graph.queries[0]
        self.assertNotIn("contract_id", cypher)
        self.assertNotIn("contract_id", params)


class ExecuteToolsInjectsContractIdServerSideTests(unittest.TestCase):
    """contract_id must be server-injected the same untrusted-input-proof
    way as tenant_id - never left for the model to have supplied."""

    def test_contract_id_from_config_overwrites_any_llm_supplied_value(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.contract_chat_agent import get_agent
        from langchain_core.messages import AIMessage

        fake_tool_call_message = AIMessage(
            content="",
            tool_calls=[{
                "name": "EnhancedContractSearch",
                # The model has no contract_id field to fill in at all
                # (not in EnhancedContractInput) - but even if it tried to
                # smuggle one in, this proves it gets overwritten.
                "args": {"summary_search": "fees", "contract_id": "SPOOFED_BY_MODEL"},
                "id": "call_1",
            }],
        )
        fake_final_message = AIMessage(content="Here is what I found.")

        fake_llm = MagicMock()
        fake_llm.bind_tools.return_value = fake_llm
        # First assistant turn proposes the tool call; second (after the
        # tool result comes back) ends the graph with no further tool
        # calls - a real two-step LangGraph run, not a mocked-out node.
        fake_llm.invoke.side_effect = [fake_tool_call_message, fake_final_message]

        with patch("backend.shared.utils.enhanced_contract_search_tool.EnhancedContractSearchTool._run") as fake_run:
            fake_run.return_value = "{}"
            graph = get_agent(fake_llm)

            config = {"configurable": {"tenant_id": "tenant_a", "contract_id": "REAL_SELECTED_CONTRACT"}}
            graph.invoke({"messages": [("human", "Analyze this contract")]}, config=config)

        self.assertTrue(fake_run.called)
        _, kwargs = fake_run.call_args
        self.assertEqual(kwargs.get("contract_id"), "REAL_SELECTED_CONTRACT")


if __name__ == "__main__":
    unittest.main()
