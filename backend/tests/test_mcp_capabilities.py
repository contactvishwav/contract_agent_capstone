import unittest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Mock Neo4j and Gemini BEFORE importing backend modules that instantiate them at module level
with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.mcp_server import search_clause_library, get_playbook_rule, fetch_contract_metadata
    from backend.shared.utils.mcp_logger import trace_id_var

class TestMCPCapabilities(unittest.IsolatedAsyncioTestCase):
    """
    Verification tests for the MCP layer (SOLID, DRY, Tracing).
    """

    def setUp(self):
        # Reset trace_id for each test
        trace_id_var.set(None)

    async def test_missing_tenant_id(self):
        """Verify that tenant_id is mandatory (Security/Access Control requirement)"""
        # Calling tool without tenant_id in kwargs
        result_json = await search_clause_library(query="test")
        result = json.loads(result_json)
        
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Missing mandatory 'tenant_id' parameter")

    @patch("backend.mcp_server.get_policy_repo")
    async def test_search_clause_library_success(self, mock_get_repo):
        """Verify successful clause search with tracing"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.search_policies_semantic.return_value = [{"id": "c1", "text": "Sample clause"}]
        
        result_json = await search_clause_library(query="liability", tenant_id="test_tenant")
        result = json.loads(result_json)
        
        self.assertTrue(result["success"])
        self.assertEqual(len(result["clauses"]), 1)
        # Ensure trace_id was generated
        self.assertIsNotNone(trace_id_var.get())

    @patch("backend.mcp_server.get_policy_repo")
    async def test_get_playbook_rule_success(self, mock_get_repo):
        """Verify playbook rule retrieval"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_rule = MagicMock()
        mock_rule.id = "r1"
        mock_rule.rule_text = "Standard rule"
        mock_rule.severity = "HIGH"
        mock_rule.rule_type = "compliance"
        
        mock_repo.get_applicable_policies.return_value = [mock_rule]
        
        result_json = await get_playbook_rule(tenant_id="test_tenant", contract_type="MSA")
        result = json.loads(result_json)
        
        self.assertTrue(result["success"])
        self.assertEqual(result["rules"][0]["text"], "Standard rule")

    @patch("backend.mcp_server.get_contract_repo")
    async def test_fetch_metadata_success(self, mock_get_repo):
        """Verify metadata fetching"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        # get_contract_by_id is `async def` on Neo4jContractRepository (see
        # backend/infrastructure/contract_repository.py:19), so it must be mocked
        # with AsyncMock, not MagicMock. MagicMock().return_value makes the call
        # return a plain dict synchronously, which previously masked a missing
        # `await` in fetch_contract_metadata (backend/mcp_server.py:122): the
        # unawaited coroutine object is truthy, so the "not found" branch never
        # fires, and json.dumps() on a coroutine used to raise TypeError in
        # production while this MagicMock-based test stayed green.
        mock_repo.get_contract_by_id = AsyncMock(return_value={"file_id": "CNT1", "parties": []})

        result_json = await fetch_contract_metadata(contract_id="CNT1", tenant_id="test_tenant")
        result = json.loads(result_json)

        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["file_id"], "CNT1")

    @patch("backend.mcp_server.get_contract_repo")
    async def test_cross_tenant_access_denied(self, mock_get_repo):
        """Verify that accessing a contract with the wrong tenant_id fails (Data Isolation)"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        # Simulate repository returning None when tenant_id doesn't match.
        # AsyncMock required here too, see comment in test_fetch_metadata_success.
        mock_repo.get_contract_by_id = AsyncMock(return_value=None)

        result_json = await fetch_contract_metadata(contract_id="CNT1", tenant_id="wrong_tenant")
        result = json.loads(result_json)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Contract not found")

    @patch("backend.mcp_server.get_contract_repo")
    async def test_fetch_metadata_awaits_async_repo_call(self, mock_get_repo):
        """
        Regression test for the missing `await` bug: fetch_contract_metadata must
        actually await get_contract_by_id() rather than treating its return value
        as already-resolved data. A MagicMock stand-in for the async repo method
        would return an unawaited coroutine that is truthy (so the "not found"
        error path never triggers) and isn't JSON-serializable, so this test uses
        AsyncMock specifically to exercise the real async call path end to end.
        """
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_contract_by_id = AsyncMock(return_value={"file_id": "CNT_ASYNC", "parties": []})

        result_json = await fetch_contract_metadata(contract_id="CNT_ASYNC", tenant_id="test_tenant")
        result = json.loads(result_json)

        # If the `await` were missing, `metadata` would be a coroutine object:
        # truthy (masking the "not found" branch) and unserializable, so this
        # call would raise TypeError inside json.dumps and be caught by the
        # tool's own except-block, surfacing success=False here instead.
        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["file_id"], "CNT_ASYNC")
        mock_repo.get_contract_by_id.assert_awaited_once_with("CNT_ASYNC", tenant_id="test_tenant")

if __name__ == "__main__":
    unittest.main()
