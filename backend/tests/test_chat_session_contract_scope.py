import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.main as main
    from backend.api import chat_sessions
    from backend.governance.auth import TokenIdentity


class ContractScopeNormalizationTests(unittest.TestCase):
    def test_all_contract_representations_normalize_to_none(self):
        self.assertIsNone(chat_sessions.normalize_contract_scope(None))
        self.assertIsNone(chat_sessions.normalize_contract_scope("__all_contracts__"))
        self.assertIsNone(chat_sessions.normalize_contract_scope("  "))


class ChatRunContractScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN")
        self.llm_mgr = MagicMock()
        self.llm_mgr.agents = {"gemini-2.5-flash": MagicMock()}
        self.llm_mgr.raw_llms = {"gemini-2.5-flash": MagicMock()}

    async def _run(self, session_contract, request_contract):
        session_repo = MagicMock()
        session_repo.get_session.return_value = {
            "session_id": "session-1",
            "tenant_id": "tenant_a",
            "contract_id": session_contract,
        }
        payload = main.RunPayload(
            model="gemini-2.5-flash",
            prompt="question",
            session_id="session-1",
            contract_id=request_contract,
        )
        with patch.object(main, "Neo4jChatSessionRepository", return_value=session_repo), \
             patch.object(main, "contract_exists_for_tenant", return_value=True) as owns:
            response = await main.run(payload, llm_mgr=self.llm_mgr, identity=self.identity)
        return response, owns

    async def test_matching_specific_contract_is_allowed(self):
        response, owns = await self._run("contract-a", "contract-a")
        self.assertEqual(response.media_type, "text/event-stream")
        owns.assert_called_once_with("contract-a", "tenant_a")

    async def test_contract_a_session_rejects_contract_b_without_starting_runner(self):
        with patch.object(main, "runner") as runner:
            with self.assertRaises(HTTPException) as caught:
                await self._run("contract-a", "contract-b")
        self.assertEqual(caught.exception.status_code, 409)
        runner.assert_not_called()

    async def test_all_contracts_session_rejects_specific_contract(self):
        with self.assertRaises(HTTPException) as caught:
            await self._run(None, "contract-a")
        self.assertEqual(caught.exception.status_code, 409)

    async def test_specific_contract_session_rejects_all_contracts(self):
        with self.assertRaises(HTTPException) as caught:
            await self._run("contract-a", None)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_missing_or_cross_tenant_contract_is_not_found(self):
        session_repo = MagicMock()
        session_repo.get_session.return_value = {
            "session_id": "session-1", "tenant_id": "tenant_a", "contract_id": "contract-a"
        }
        payload = main.RunPayload(
            model="gemini-2.5-flash", prompt="question",
            session_id="session-1", contract_id="contract-a",
        )
        with patch.object(main, "Neo4jChatSessionRepository", return_value=session_repo), \
             patch.object(main, "contract_exists_for_tenant", return_value=False), \
             patch.object(main, "runner") as runner:
            with self.assertRaises(HTTPException) as caught:
                await main.run(payload, llm_mgr=self.llm_mgr, identity=self.identity)
        self.assertEqual(caught.exception.status_code, 404)
        runner.assert_not_called()

    async def test_rejection_does_not_read_or_append_session_history(self):
        session_repo = MagicMock()
        session_repo.get_session.return_value = {
            "session_id": "session-1", "tenant_id": "tenant_a", "contract_id": "contract-a"
        }
        payload = main.RunPayload(
            model="gemini-2.5-flash", prompt="question",
            session_id="session-1", contract_id="contract-b",
        )
        with patch.object(main, "Neo4jChatSessionRepository", return_value=session_repo), \
             patch.object(main, "runner") as runner:
            with self.assertRaises(HTTPException):
                await main.run(payload, llm_mgr=self.llm_mgr, identity=self.identity)
        session_repo.list_messages.assert_not_called()
        session_repo.append_message.assert_not_called()
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
