"""
ChatSession/ChatMessage schema migration - persistent, per-document
Contract Chat sessions (see backend/infrastructure/chat_session_repository.py
for the schema this backs).
"""
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


class ChatSessionSchemaMigration:
    def __init__(self):
        self.repository = Neo4jContractRepository()

    def migrate(self):
        try:
            self._create_constraints()
            self._create_indexes()
            logger.info("Chat session schema migration completed")
        except Exception as e:
            logger.error(f"Chat session schema migration failed: {e}")
            raise

    def _create_constraints(self):
        queries = [
            "CREATE CONSTRAINT chat_session_id_unique IF NOT EXISTS FOR (s:ChatSession) REQUIRE s.session_id IS UNIQUE",
            "CREATE CONSTRAINT chat_message_id_unique IF NOT EXISTS FOR (m:ChatMessage) REQUIRE m.message_id IS UNIQUE",
        ]
        for query in queries:
            try:
                self.repository.graph.query(query)
                logger.info(f"Created constraint: {query}")
            except Exception as e:
                logger.warning(f"Constraint may already exist: {e}")

    def _create_indexes(self):
        queries = [
            "CREATE INDEX chat_session_tenant IF NOT EXISTS FOR (s:ChatSession) ON (s.tenant_id)",
            "CREATE INDEX chat_session_tenant_contract IF NOT EXISTS FOR (s:ChatSession) ON (s.tenant_id, s.contract_id)",
            "CREATE INDEX chat_session_tenant_updated IF NOT EXISTS FOR (s:ChatSession) ON (s.tenant_id, s.updated_at)",
            "CREATE INDEX chat_message_tenant IF NOT EXISTS FOR (m:ChatMessage) ON (m.tenant_id)",
        ]
        for query in queries:
            try:
                self.repository.graph.query(query)
                logger.info(f"Created index: {query}")
            except Exception as e:
                logger.warning(f"Index may already exist: {e}")


def run_migration():
    ChatSessionSchemaMigration().migrate()


if __name__ == "__main__":
    run_migration()
