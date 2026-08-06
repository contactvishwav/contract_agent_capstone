"""
Single coordinated, versioned entry point for all schema migrations.

Previously: 8 independent migration modules (7 flagged by docs/ENTERPRISE_
READINESS.md plus this file's own new contract_constraints_migration),
with only multi_level_embeddings reachable from backend/run_migration.py -
the other 6 (7 counting fix_enterprise_relationships) had to be invoked
directly (`python -m backend.migrations.X`), and nothing anywhere recorded
which migrations had already been applied to a given database.

Tracks applied migrations via a :SchemaMigration {name, applied_at} node in
Neo4j (MERGE-keyed by name) - idempotent and cheap, so re-running this
script is always safe: already-applied migrations are skipped, and a
migration that raises partway through is never marked as applied (so a
subsequent run retries it rather than silently skipping a broken state).

Usage:
    python -m backend.migrations.run_all_migrations            # run all pending
    python -m backend.migrations.run_all_migrations --status    # show what's applied
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


def _run_contract_constraints():
    from backend.migrations.contract_constraints_migration import ContractConstraintsMigration
    ContractConstraintsMigration().migrate()


def _run_section_schema():
    from backend.migrations.section_schema_migration import SectionSchemaMigration
    SectionSchemaMigration().migrate()


def _run_clause_schema():
    from backend.migrations.clause_schema_migration import ClauseSchemaMigration
    ClauseSchemaMigration().migrate()


def _run_audit_error_schema():
    from backend.migrations.audit_error_schema_migration import AuditErrorSchemaMigration
    AuditErrorSchemaMigration().migrate()


def _run_enterprise_schema():
    from backend.migrations.enterprise_schema_migration import EnterpriseSchemaMigration
    EnterpriseSchemaMigration().migrate_enterprise_schema()


def _run_fix_enterprise_relationships():
    from backend.migrations.fix_enterprise_relationships import FixEnterpriseRelationships
    FixEnterpriseRelationships().fix_relationships()


def _run_multi_level_embeddings():
    from backend.migrations.multi_level_embeddings import upgrade_schema
    upgrade_schema()


def _run_phase2_phase3_schema():
    from backend.migrations.phase2_phase3_schema import Phase2Phase3SchemaMigration
    Phase2Phase3SchemaMigration().migrate_schema()


def _run_vector_indexes():
    from backend.migrations.vector_index_migration import VectorIndexMigration
    VectorIndexMigration().migrate()


def _run_embedding_range_index_fix():
    from backend.migrations.embedding_range_index_fix import EmbeddingRangeIndexFixMigration
    EmbeddingRangeIndexFixMigration().migrate()


def _run_user_schema():
    from backend.migrations.user_schema_migration import UserSchemaMigration
    UserSchemaMigration().migrate()


# Ordered: the new Contract constraint first (safe to run anytime, no
# dependencies), then the base schema pieces, then fix_enterprise_
# relationships (depends on enterprise_schema's sample data existing
# first), then the later embeddings/phase2-3 additions - matching the order
# they were historically introduced (confirmed via git log: all 7 original
# scripts landed in a single commit, so this ordering reflects logical
# dependency, not commit history). Vector indexes run last since they only
# need the node labels (Contract/Section/Clause/Chunk/PolicyDocument) to
# exist, not any particular data in them. embedding_range_index_fix runs
# right after vector_indexes: it drops the stale RANGE indexes on the same
# embedding properties (multi_level_embeddings.py) once a real vector index
# exists to replace them, found live in production (a 1536-dim embedding
# write threw "Property value is too large to index" against the old RANGE
# index even though a working vector index already existed alongside it).
MIGRATIONS = [
    ("contract_constraints", _run_contract_constraints),
    ("section_schema", _run_section_schema),
    ("clause_schema", _run_clause_schema),
    ("audit_error_schema", _run_audit_error_schema),
    ("enterprise_schema", _run_enterprise_schema),
    ("fix_enterprise_relationships", _run_fix_enterprise_relationships),
    ("multi_level_embeddings", _run_multi_level_embeddings),
    ("phase2_phase3_schema", _run_phase2_phase3_schema),
    ("vector_indexes", _run_vector_indexes),
    ("embedding_range_index_fix", _run_embedding_range_index_fix),
    ("user_schema", _run_user_schema),
]


class MigrationRunner:
    def __init__(self, graph=None):
        self.graph = graph or Neo4jContractRepository().graph

    def _is_applied(self, name: str) -> bool:
        result = self.graph.query(
            "MATCH (m:SchemaMigration {name: $name}) RETURN m.name as name", {"name": name}
        )
        return bool(result)

    def _mark_applied(self, name: str):
        self.graph.query(
            "MERGE (m:SchemaMigration {name: $name}) SET m.applied_at = datetime()", {"name": name}
        )

    def run_pending(self, migrations=None):
        """
        Run every migration not yet marked applied, in order. Stops (without
        marking the failing migration as applied) on the first failure, so
        a partially-applied migration is never silently recorded as done -
        the caller sees the exception and can re-run once fixed, and the
        next run will retry exactly that migration rather than skipping it.
        """
        migrations = migrations if migrations is not None else MIGRATIONS
        applied, skipped = [], []
        for name, fn in migrations:
            if self._is_applied(name):
                skipped.append(name)
                continue
            logger.info(f"Running migration: {name}")
            fn()
            self._mark_applied(name)
            applied.append(name)
            logger.info(f"Migration applied: {name}")
        return {"applied": applied, "skipped_already_applied": skipped}

    def status(self, migrations=None):
        migrations = migrations if migrations is not None else MIGRATIONS
        return {name: self._is_applied(name) for name, _ in migrations}


def main():
    runner = MigrationRunner()
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        for name, is_applied in runner.status().items():
            print(f"{'[applied]' if is_applied else '[pending]'} {name}")
        return

    result = runner.run_pending()
    print(f"Applied: {result['applied']}")
    print(f"Already applied (skipped): {result['skipped_already_applied']}")


if __name__ == "__main__":
    main()
