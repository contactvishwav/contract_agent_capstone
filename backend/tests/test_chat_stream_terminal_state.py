import unittest
from unittest.mock import MagicMock, patch


SENSITIVE_FAILURE = "failure contains raw prompt and contract"


async def _broken_runner(**_kwargs):
    if False:
        yield ""
    raise RuntimeError(SENSITIVE_FAILURE)


class ChatStreamTerminalStateTests(unittest.IsolatedAsyncioTestCase):
    def test_cancelled_terminal_is_tenant_scoped_and_completion_wins(self):
        from backend.main import _persist_chat_terminal_state

        repository = MagicMock()
        repository.list_messages.return_value = [{"role": "user_message"}]
        repository.append_message.return_value = {"message_id": "MESSAGE_CANCELLED"}

        with patch("backend.main.Neo4jChatSessionRepository", return_value=repository):
            persisted = _persist_chat_terminal_state(
                "SESSION_A", "tenant-a", "gemini-2.5-flash",
                "Generation stopped", "cancelled",
            )

        self.assertTrue(persisted)
        repository.list_messages.assert_called_once_with("SESSION_A", "tenant-a")
        repository.append_message.assert_called_once_with(
            "SESSION_A",
            "tenant-a",
            role="ai_message",
            content="Generation stopped",
            model="gemini-2.5-flash",
            terminal_status="cancelled",
        )

        repository.reset_mock()
        repository.list_messages.return_value = [{"role": "ai_message", "terminal_status": "passed"}]
        with patch("backend.main.Neo4jChatSessionRepository", return_value=repository):
            persisted = _persist_chat_terminal_state(
                "SESSION_A", "tenant-a", "gemini-2.5-flash",
                "Generation stopped", "cancelled",
            )

        self.assertFalse(persisted)
        repository.append_message.assert_not_called()

    async def test_failed_stream_persists_safe_terminal_message_and_ends_sse(self):
        from backend.main import resilient_runner

        repository = MagicMock()
        repository.list_messages.return_value = [{"role": "user_message"}]

        with patch("backend.main.runner", _broken_runner), \
             patch("backend.main.Neo4jChatSessionRepository", return_value=repository), \
             self.assertLogs("backend.main", level="ERROR") as logs:
            events = [event async for event in resilient_runner(
                model="gemini-2.5-flash",
                prompt="private prompt",
                history="[]",
                llm_mgr=MagicMock(),
                tenant_id="tenant-a",
                chat_session_id="SESSION_A",
            )]

        repository.append_message.assert_called_once_with(
            "SESSION_A",
            "tenant-a",
            role="ai_message",
            content="Response failed before completion. Please retry.",
            model="gemini-2.5-flash",
            terminal_status="generation_failed",
        )
        self.assertIn('"type": "error"', events[0])
        self.assertIn('"status": "generation_failed"', events[0])
        self.assertIn('"type": "end"', events[1])
        self.assertIn('"status": "generation_failed"', events[1])
        self.assertNotIn(SENSITIVE_FAILURE, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
