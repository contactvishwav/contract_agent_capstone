"""
User node constraint migration.

Adds the uniqueness guarantee UserRepository.create_user's own
existence-check is only a friendly, non-atomic approximation of - two
concurrent registrations for the same username must not both succeed, and
only a real database constraint can guarantee that.
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


class UserSchemaMigration:
    """Adds the User.username uniqueness constraint."""

    def __init__(self):
        self.repository = Neo4jContractRepository()

    def migrate(self):
        try:
            self._add_username_constraint()
            logger.info("User schema migration completed")
        except Exception as e:
            logger.error(f"User schema migration failed: {e}")
            raise

    def _add_username_constraint(self):
        query = "CREATE CONSTRAINT user_username_unique IF NOT EXISTS FOR (u:User) REQUIRE u.username IS UNIQUE"
        try:
            self.repository.graph.query(query)
            logger.info("User.username uniqueness constraint created")
        except Exception as e:
            logger.warning(f"Query failed (may already exist): {e}")


def run_migration():
    migration = UserSchemaMigration()
    migration.migrate()


if __name__ == "__main__":
    run_migration()
