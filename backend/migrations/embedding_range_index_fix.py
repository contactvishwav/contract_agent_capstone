"""
Fixes a real production bug found live during GCP deployment verification:
four embedding-array properties (Contract.document_embedding,
Contract.summary_embedding, Section.embedding, Clause.embedding) were still
covered by the plain RANGE indexes multi_level_embeddings.py created before
native Neo4j vector indexes existed in this codebase (vector_index_
migration.py, P3 item 16). A RANGE index has a small per-property size
limit that a 1536-dim float array exceeds, so any write to one of these
four properties throws "Property value is too large to index" - confirmed
against the live AuraDB instance via SHOW INDEXES, which still listed all
four as ONLINE RANGE indexes.

Section.embedding and Clause.embedding already have a real VECTOR index
(created by vector_index_migration.py) sitting alongside the broken RANGE
index on the very same property - adding the vector index never removed
the old one, so the write still failed against the RANGE index regardless.
Contract.document_embedding/summary_embedding had no vector index at all.

Fix: drop the four stale RANGE indexes (they provide no query value anyway
- nothing in this codebase does a RANGE lookup on an embedding array), and
create the two missing Contract-level vector indexes via the same
VECTOR_INDEXES-driven migration already used for every other embedding
property (vector_index_migration.py) - re-running it is a safe no-op for
the three entries that already have their vector index.
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.migrations.vector_index_migration import VectorIndexMigration

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


# (index_name, label, property) - the four RANGE indexes to drop. Index
# names match exactly what multi_level_embeddings.py created.
STALE_RANGE_INDEXES = [
    ("contract_document_embedding", "Contract", "document_embedding"),
    ("contract_summary_embedding", "Contract", "summary_embedding"),
    ("section_embedding", "Section", "embedding"),
    ("clause_embedding", "Clause", "embedding"),
]


class EmbeddingRangeIndexFixMigration:
    """Drops the stale RANGE indexes on embedding-array properties and
    ensures every embedding property has a real vector index instead."""

    def __init__(self):
        self.repository = Neo4jContractRepository()

    def migrate(self):
        for index_name, label, prop in STALE_RANGE_INDEXES:
            self._drop_range_index(index_name, label, prop)

        # Creates the two new Contract-level vector indexes registered in
        # vector_index_config.py's VECTOR_INDEXES; IF NOT EXISTS makes
        # re-issuing the other three (already created by the original
        # vector_indexes migration) a harmless no-op.
        vector_migration = VectorIndexMigration()
        vector_migration.repository = self.repository
        vector_migration.migrate()

        logger.info("Embedding range index fix migration completed")

    def _drop_range_index(self, index_name: str, label: str, prop: str):
        query = f"DROP INDEX {index_name} IF EXISTS"
        try:
            self.repository.graph.query(query)
            logger.info(f"Dropped stale RANGE index: {index_name} ({label}.{prop})")
        except Exception as e:
            logger.warning(f"Failed to drop RANGE index {index_name} (may not exist): {e}")


def run_migration():
    EmbeddingRangeIndexFixMigration().migrate()


if __name__ == "__main__":
    run_migration()
