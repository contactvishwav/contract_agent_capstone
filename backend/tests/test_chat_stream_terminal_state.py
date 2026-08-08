import unittest
from unittest.mock import MagicMock, patch


SENSITIVE_FAILURE = "failure contains raw prompt and contract"


async def _broken_runner(**_kwargs):
    if False:
        yield ""
    raise RuntimeError(SENSITIVE_FAILURE)


class ChatStreamTerminalStateTests(unittest.IsolatedAsyncioTestCase):
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
        )
        self.assertIn('"type": "error"', events[0])
        self.assertIn('"type": "end"', events[1])
        self.assertNotIn(SENSITIVE_FAILURE, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
