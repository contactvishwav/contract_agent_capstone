"""
ChatSessionSchemaMigration: asserts it issues the expected constraints/
indexes, and - separately - that it's actually registered in
run_all_migrations.MIGRATIONS. A migration class that's written but never
added to that list is a real, silent failure mode this codebase has hit
before (see test_migration_consolidation.py's history), so the
registration itself gets its own regression guard here, not just an
assumption that writing the class is enough.
"""

import unittest
from unittest.mock import patch

from backend.tests.test_migration_consolidation import FakeMigrationGraph

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.migrations.chat_session_schema_migration import ChatSessionSchemaMigration


class ChatSessionSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            self.migration = ChatSessionSchemaMigration()
        self.fake_graph = FakeMigrationGraph()
        self.migration.repository.graph = self.fake_graph

    def test_creates_uniqueness_constraints_on_both_id_fields(self):
        self.migration.migrate()

        constraint_queries = [q for q in self.fake_graph.issued_queries if "CREATE CONSTRAINT" in q]
        self.assertEqual(len(constraint_queries), 2)

        session_constraint = next(q for q in constraint_queries if "chat_session_id_unique" in q)
        self.assertIn("FOR (s:ChatSession)", session_constraint)
        self.assertIn("REQUIRE s.session_id IS UNIQUE", session_constraint)

        message_constraint = next(q for q in constraint_queries if "chat_message_id_unique" in q)
        self.assertIn("FOR (m:ChatMessage)", message_constraint)
        self.assertIn("REQUIRE m.message_id IS UNIQUE", message_constraint)

    def test_creates_tenant_scoping_indexes(self):
        self.migration.migrate()

        index_queries = [q for q in self.fake_graph.issued_queries if "CREATE INDEX" in q]
        index_names = " ".join(index_queries)

        self.assertIn("chat_session_tenant ", index_names)
        self.assertIn("ON (s.tenant_id)", index_names)
        self.assertIn("chat_session_tenant_contract", index_names)
        self.assertIn("ON (s.tenant_id, s.contract_id)", index_names)
        self.assertIn("chat_session_tenant_updated", index_names)
        self.assertIn("ON (s.tenant_id, s.updated_at)", index_names)
        self.assertIn("chat_message_tenant ", index_names)
        self.assertIn("ON (m.tenant_id)", index_names)

    def test_migrate_is_safe_to_run_twice(self):
        self.migration.migrate()
        self.migration.migrate()  # IF NOT EXISTS everywhere - must not raise


class ChatSessionSchemaMigrationRegistrationTests(unittest.TestCase):
    def test_registered_in_run_all_migrations(self):
        from backend.migrations.run_all_migrations import MIGRATIONS
        names = [name for name, _ in MIGRATIONS]
        self.assertIn("chat_session_schema", names)

    def test_registered_wrapper_actually_calls_the_migration_class(self):
        from backend.migrations import run_all_migrations as run_all_migrations_module
        wrapper = dict(run_all_migrations_module.MIGRATIONS)["chat_session_schema"]

        with patch(
            "backend.migrations.chat_session_schema_migration.ChatSessionSchemaMigration"
        ) as MockMigration:
            wrapper()
            MockMigration.return_value.migrate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
