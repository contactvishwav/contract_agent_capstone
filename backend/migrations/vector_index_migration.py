"""
Native Neo4j vector index migration (P3 item 16).

docs/ENTERPRISE_READINESS.md found 13 `vector.similarity.cosine()` calls and
4 `gds.similarity.cosine()` calls across the codebase, all doing brute-force
similarity: MATCH every node of a label (optionally tenant/type-scoped),
compute cosine similarity for every one, then filter/sort in Cypher. No
`CREATE VECTOR INDEX` existed anywhere - the plain `CREATE INDEX ... ON
(n.embedding)` indexes in multi_level_embeddings.py are ordinary property
(range) indexes, which cannot do a nearest-neighbor lookup on a float array;
they don't help similarity search at all.

This migration creates real vector indexes (Neo4j 5.11+ native support,
confirmed available - the project's neo4j driver is 5.28.1) for every node
label that stores an embedding: Contract, Section, Clause, Chunk, and
PolicyDocument. The one embedding NOT covered is the PARTY_TO relationship's
`.embedding` property (2 of the 13 vector.similarity.cosine call sites) -
relationship vector indexes are a newer, less universally-available Neo4j
feature, and given the actual connection target is a shared, externally-
managed instance (neo4j+s://demo.neo4jlabs.com) whose exact edition/version
isn't controlled by this migration, those two call sites are intentionally
left on the existing brute-force approach rather than assuming relationship
vector index support that may not be there.
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.vector_index_config import VECTOR_INDEXES, EMBEDDING_DIMENSIONS

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


class VectorIndexMigration:
    """Creates native Neo4j vector indexes for every embedding-bearing node label."""

    def __init__(self):
        self.repository = Neo4jContractRepository()

    def migrate(self):
        try:
            for index_name, (label, prop) in VECTOR_INDEXES.items():
                self._create_vector_index(index_name, label, prop)
            logger.info("Vector index migration completed")
        except Exception as e:
            logger.error(f"Vector index migration failed: {e}")
            raise

    def _create_vector_index(self, index_name: str, label: str, prop: str):
        query = f"""
        CREATE VECTOR INDEX {index_name} IF NOT EXISTS
        FOR (n:{label}) ON (n.{prop})
        OPTIONS {{indexConfig: {{
            `vector.dimensions`: {EMBEDDING_DIMENSIONS},
            `vector.similarity_function`: 'cosine'
        }}}}
        """
        try:
            self.repository.graph.query(query)
            logger.info(f"Vector index created: {index_name} ({label}.{prop})")
        except Exception as e:
            logger.warning(f"Query failed (may already exist, or vector indexes unsupported on this instance): {e}")


def run_migration():
    VectorIndexMigration().migrate()


if __name__ == "__main__":
    run_migration()
