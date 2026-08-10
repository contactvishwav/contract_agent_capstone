"""
ChatAttachment schema migration - Contract Chat image attachments (ADR-008).
See backend/infrastructure/chat_session_repository.py's attachment methods
and backend/infrastructure/chat_attachment_storage.py for the schema this
backs: (:ChatMessage)-[:HAS_ATTACHMENT]->(:ChatAttachment).
"""
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


class ChatAttachmentSchemaMigration:
    def __init__(self):
        self.repository = Neo4jContractRepository()

    def migrate(self):
        try:
            self._create_constraints()
            self._create_indexes()
            logger.info("Chat attachment schema migration completed")
        except Exception as e:
            logger.error(f"Chat attachment schema migration failed: {e}")
            raise

    def _create_constraints(self):
        queries = [
            "CREATE CONSTRAINT chat_attachment_id_unique IF NOT EXISTS FOR (a:ChatAttachment) REQUIRE a.attachment_id IS UNIQUE",
        ]
        for query in queries:
            try:
                self.repository.graph.query(query)
                logger.info(f"Created constraint: {query}")
            except Exception as e:
                logger.warning(f"Constraint may already exist: {e}")

    def _create_indexes(self):
        queries = [
            "CREATE INDEX chat_attachment_tenant IF NOT EXISTS FOR (a:ChatAttachment) ON (a.tenant_id)",
            "CREATE INDEX chat_attachment_tenant_session IF NOT EXISTS FOR (a:ChatAttachment) ON (a.tenant_id, a.session_id)",
        ]
        for query in queries:
            try:
                self.repository.graph.query(query)
                logger.info(f"Created index: {query}")
            except Exception as e:
                logger.warning(f"Index may already exist: {e}")


def run_migration():
    ChatAttachmentSchemaMigration().migrate()


if __name__ == "__main__":
    run_migration()
