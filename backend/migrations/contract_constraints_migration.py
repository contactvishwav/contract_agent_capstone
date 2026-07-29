"""
Contract node constraints migration.

docs/ENTERPRISE_READINESS.md found exactly 4 uniqueness constraints in the
whole schema (tenant_id, section_id, clause_id, legal_decision_id) - none on
Contract itself. contract_repository.py's store_contract uses CREATE (not
MERGE) for new Contract nodes, keyed by a UUID-based file_id
(f"UPLOADED_{uuid.uuid4().hex[:8].upper()}_{date}") that every read/lookup
query then assumes is unique - nothing in the database actually enforces
that. A single-property constraint (not a composite tenant_id+file_id key)
matches how file_id is generated: globally, not scoped per tenant.
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


class ContractConstraintsMigration:
    """Adds the missing Contract.file_id uniqueness constraint."""

    def __init__(self):
        self.repository = Neo4jContractRepository()

    def migrate(self):
        """Apply Contract constraints."""
        try:
            self._add_file_id_constraint()
            logger.info("Contract constraints migration completed")
        except Exception as e:
            logger.error(f"Contract constraints migration failed: {e}")
            raise

    def _add_file_id_constraint(self):
        query = "CREATE CONSTRAINT contract_file_id_unique IF NOT EXISTS FOR (c:Contract) REQUIRE c.file_id IS UNIQUE"
        try:
            self.repository.graph.query(query)
            logger.info("Contract.file_id uniqueness constraint created")
        except Exception as e:
            logger.warning(f"Query failed (may already exist): {e}")


def run_migration():
    migration = ContractConstraintsMigration()
    migration.migrate()


if __name__ == "__main__":
    run_migration()
