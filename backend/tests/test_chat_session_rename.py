import asyncio
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api import chat_sessions
    from backend.governance.auth import TokenIdentity


class ChatSessionRenameTests(unittest.TestCase):
    def setUp(self):
        self.identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN")

    def test_title_normalization_and_limits(self):
        self.assertEqual(chat_sessions.RenameSessionRequest(title="  Payment   terms  ").title, "Payment terms")
        with self.assertRaises(ValueError):
            chat_sessions.RenameSessionRequest(title="   ")
        with self.assertRaises(ValueError):
            chat_sessions.RenameSessionRequest(title="x" * 121)

    def test_cross_tenant_or_missing_session_is_same_404(self):
        repository = MagicMock()
        repository.rename_session.return_value = None
        with patch.object(chat_sessions, "repository", repository):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(chat_sessions.rename_session(
                    "SESSION_OTHER", chat_sessions.RenameSessionRequest(title="New name"), self.identity,
                ))
        self.assertEqual(caught.exception.status_code, 404)
        repository.rename_session.assert_called_once_with("SESSION_OTHER", "tenant_a", "New name")
