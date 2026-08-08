import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.mcp.client_bridge import call_mcp_tool
    from backend.mcp.security import assert_standalone_server_allowed


# Match the existing real in-memory bridge regression suite: pytest's
# per-test warning reset can make the MCP SDK emit its own utcnow()
# deprecation warning on every session-loop iteration and never settle.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class InProcessMcpIdentityTests(unittest.TestCase):
    @patch("backend.mcp_server.get_policy_repo")
    def test_caller_tenant_argument_cannot_override_authenticated_tenant(self, get_repo):
        repo = MagicMock()
        repo.search_policies_semantic.return_value = []
        get_repo.return_value = repo

        payload = asyncio.run(call_mcp_tool(
            "search_clause_library",
            {"query": "liability", "tenant_id": "attacker"},
            authenticated_tenant_id="tenant_a",
        ))

        self.assertTrue(payload["success"])
        repo.search_policies_semantic.assert_called_once_with("liability", "tenant_a")

    @patch("backend.mcp_server.get_policy_repo")
    def test_missing_authenticated_bridge_identity_fails_before_tool(self, get_repo):
        payload = asyncio.run(call_mcp_tool(
            "search_clause_library", {"query": "liability", "tenant_id": "tenant_a"}
        ))
        self.assertFalse(payload["success"])
        get_repo.assert_not_called()

    @patch("backend.mcp_server.get_policy_repo")
    def test_policy_and_clause_paths_use_authenticated_tenant(self, get_repo):
        repo = MagicMock()
        repo.search_policies_semantic.return_value = []
        repo.get_applicable_policies.return_value = []
        get_repo.return_value = repo

        asyncio.run(call_mcp_tool(
            "search_clause_library", {"query": "q"},
            authenticated_tenant_id="tenant_a",
        ))
        asyncio.run(call_mcp_tool(
            "get_playbook_rule", {"contract_type": "NDA"},
            authenticated_tenant_id="tenant_a",
        ))

        repo.search_policies_semantic.assert_called_once_with("q", "tenant_a")
        repo.get_applicable_policies.assert_called_once_with("tenant_a", "NDA")

    @patch("backend.mcp_server.get_contract_repo")
    def test_metadata_path_uses_authenticated_tenant(self, get_repo):
        repo = MagicMock()
        repo.get_contract_by_id = AsyncMock(return_value={"file_id": "c1"})
        get_repo.return_value = repo

        payload = asyncio.run(call_mcp_tool(
            "fetch_contract_metadata",
            {"contract_id": "c1", "tenant_id": "attacker"},
            authenticated_tenant_id="tenant_a",
        ))

        self.assertTrue(payload["success"])
        repo.get_contract_by_id.assert_awaited_once_with("c1", tenant_id="tenant_a")

    @patch("backend.mcp_server.get_precedent_matcher")
    def test_precedent_path_uses_authenticated_tenant(self, get_matcher):
        matcher = MagicMock()
        matcher._run.return_value = "[]"
        get_matcher.return_value = matcher

        payload = asyncio.run(call_mcp_tool(
            "search_prior_approved_clauses",
            {"clause_text": "sample", "tenant_id": "attacker"},
            authenticated_tenant_id="tenant_a",
        ))

        self.assertTrue(payload["success"])
        matcher._run.assert_called_once()
        self.assertEqual(matcher._run.call_args.kwargs["tenant_id"], "tenant_a")


class StandaloneMcpPolicyTests(unittest.TestCase):
    def test_production_standalone_is_disabled_even_with_local_flag(self):
        with patch.dict(os.environ, {
            "ENVIRONMENT": "production", "MCP_STANDALONE_LOCAL_ONLY": "true"
        }, clear=False):
            with self.assertRaises(RuntimeError):
                assert_standalone_server_allowed()

    def test_local_standalone_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=True):
            with self.assertRaises(RuntimeError):
                assert_standalone_server_allowed()

        with patch.dict(os.environ, {
            "ENVIRONMENT": "development", "MCP_STANDALONE_LOCAL_ONLY": "true"
        }, clear=True):
            assert_standalone_server_allowed()

    def test_direct_production_tool_call_cannot_self_assert_tenant(self):
        from backend.mcp_server import search_clause_library

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False), \
             patch("backend.mcp_server.get_policy_repo") as get_repo:
            result = json.loads(asyncio.run(
                search_clause_library(query="q", tenant_id="self_asserted")
            ))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "Authenticated MCP tenant is required")
        get_repo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
