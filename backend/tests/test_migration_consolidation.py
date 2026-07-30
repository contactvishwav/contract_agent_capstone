"""
Regression tests for item 14 in docs/ENTERPRISE_READINESS.md's punch list:

1. No uniqueness constraint existed on Contract.file_id, even though every
   read/lookup query (contract_repository.py) assumes it's unique and
   store_contract uses CREATE (not MERGE) for new Contract nodes.

2. 7 independent migration scripts existed with only one
   (multi_level_embeddings) reachable from backend/run_migration.py, and no
   tracking of which migrations had already been applied to a given
   database - re-running a script was the only way to know if it was safe
   (idempotent via each script's own MERGE/IF NOT EXISTS guards), and two of
   the seven (section_schema_migration.py, clause_schema_migration.py)
   aren't even idempotent for their sample-data blocks.

Uses the FakeGraph pattern already established in
test_audit_validation_error_tracking.py / test_policy_repository_tenant_
isolation.py to exercise the real Cypher-issuing code deterministically,
without a live Neo4j instance.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.migrations.contract_constraints_migration import ContractConstraintsMigration
    from backend.migrations.run_all_migrations import MigrationRunner


class FakeMigrationGraph:
    """Records every issued query; simulates :SchemaMigration tracking
    nodes with a plain in-memory set, matching real MERGE/MATCH semantics
    closely enough for these tests (idempotent MERGE, presence-checking MATCH)."""

    def __init__(self):
        self.issued_queries = []
        self.applied_migrations = set()

    def query(self, cypher, params=None):
        params = params or {}
        self.issued_queries.append(cypher)

        if "MATCH (m:SchemaMigration" in cypher:
            name = params.get("name")
            return [{"name": name}] if name in self.applied_migrations else []

        if "MERGE (m:SchemaMigration" in cypher:
            self.applied_migrations.add(params.get("name"))
            return []

        return []


class ContractFileIdConstraintTests(unittest.TestCase):
    def test_migrate_issues_file_id_uniqueness_constraint(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            migration = ContractConstraintsMigration()
        fake_graph = FakeMigrationGraph()
        migration.repository.graph = fake_graph

        migration.migrate()

        constraint_queries = [q for q in fake_graph.issued_queries if "CREATE CONSTRAINT" in q]
        self.assertEqual(len(constraint_queries), 1)
        self.assertIn("contract_file_id_unique", constraint_queries[0])
        self.assertIn("FOR (c:Contract)", constraint_queries[0])
        self.assertIn("REQUIRE c.file_id IS UNIQUE", constraint_queries[0])


class MigrationRunnerVersioningTests(unittest.TestCase):
    def test_pending_migration_runs_and_gets_marked_applied(self):
        fake_graph = FakeMigrationGraph()
        runner = MigrationRunner(graph=fake_graph)
        call_count = {"n": 0}

        def fake_migration():
            call_count["n"] += 1

        result = runner.run_pending(migrations=[("test_migration", fake_migration)])

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(result["applied"], ["test_migration"])
        self.assertEqual(result["skipped_already_applied"], [])
        self.assertTrue(runner._is_applied("test_migration"))

    def test_already_applied_migration_is_skipped_not_rerun(self):
        fake_graph = FakeMigrationGraph()
        fake_graph.applied_migrations.add("already_done")
        runner = MigrationRunner(graph=fake_graph)
        call_count = {"n": 0}

        def should_never_run():
            call_count["n"] += 1

        result = runner.run_pending(migrations=[("already_done", should_never_run)])

        self.assertEqual(call_count["n"], 0, "Already-applied migration must not be re-run")
        self.assertEqual(result["skipped_already_applied"], ["already_done"])
        self.assertEqual(result["applied"], [])

    def test_failing_migration_is_not_marked_applied(self):
        fake_graph = FakeMigrationGraph()
        runner = MigrationRunner(graph=fake_graph)

        def broken_migration():
            raise RuntimeError("simulated failure mid-migration")

        with self.assertRaises(RuntimeError):
            runner.run_pending(migrations=[("broken", broken_migration)])

        self.assertFalse(
            runner._is_applied("broken"),
            "A migration that raised must not be recorded as applied - otherwise a retry would silently skip it",
        )

    def test_second_migration_still_runs_after_first_is_already_applied(self):
        fake_graph = FakeMigrationGraph()
        fake_graph.applied_migrations.add("first")
        runner = MigrationRunner(graph=fake_graph)
        calls = []

        result = runner.run_pending(migrations=[
            ("first", lambda: calls.append("first")),
            ("second", lambda: calls.append("second")),
        ])

        self.assertEqual(calls, ["second"])
        self.assertEqual(result["applied"], ["second"])
        self.assertEqual(result["skipped_already_applied"], ["first"])

    def test_all_9_real_migrations_registered_in_correct_order(self):
        from backend.migrations.run_all_migrations import MIGRATIONS
        names = [name for name, _ in MIGRATIONS]

        self.assertEqual(names[0], "contract_constraints")
        self.assertIn("fix_enterprise_relationships", names)
        self.assertLess(
            names.index("enterprise_schema"), names.index("fix_enterprise_relationships"),
            "fix_enterprise_relationships depends on enterprise_schema's sample data existing first",
        )
        self.assertIn("vector_indexes", names)
        self.assertEqual(len(names), 9)
        self.assertEqual(len(set(names)), 9, "No duplicate migration names")


if __name__ == "__main__":
    unittest.main()
