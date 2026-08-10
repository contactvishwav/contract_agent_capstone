"""
ChatAttachmentSchemaMigration: asserts it issues the expected constraint/
indexes, and - separately - that it's actually registered in
run_all_migrations.MIGRATIONS. Same registration-regression-guard rationale
as test_chat_session_schema_migration.py: a migration class written but
never added to that list is a real, silent failure mode this codebase has
hit before.
"""

import unittest
from unittest.mock import patch

from backend.tests.test_migration_consolidation import FakeMigrationGraph

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.migrations.chat_attachment_schema_migration import ChatAttachmentSchemaMigration


class ChatAttachmentSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            self.migration = ChatAttachmentSchemaMigration()
        self.fake_graph = FakeMigrationGraph()
        self.migration.repository.graph = self.fake_graph

    def test_creates_uniqueness_constraint_on_attachment_id(self):
        self.migration.migrate()

        constraint_queries = [q for q in self.fake_graph.issued_queries if "CREATE CONSTRAINT" in q]
        self.assertEqual(len(constraint_queries), 1)
        self.assertIn("chat_attachment_id_unique", constraint_queries[0])
        self.assertIn("FOR (a:ChatAttachment)", constraint_queries[0])
        self.assertIn("REQUIRE a.attachment_id IS UNIQUE", constraint_queries[0])

    def test_creates_tenant_scoping_indexes(self):
        self.migration.migrate()

        index_queries = [q for q in self.fake_graph.issued_queries if "CREATE INDEX" in q]
        index_names = " ".join(index_queries)

        self.assertIn("chat_attachment_tenant ", index_names)
        self.assertIn("ON (a.tenant_id)", index_names)
        self.assertIn("chat_attachment_tenant_session", index_names)
        self.assertIn("ON (a.tenant_id, a.session_id)", index_names)

    def test_migrate_is_safe_to_run_twice(self):
        self.migration.migrate()
        self.migration.migrate()  # IF NOT EXISTS everywhere - must not raise


class ChatAttachmentSchemaMigrationRegistrationTests(unittest.TestCase):
    def test_registered_in_run_all_migrations(self):
        from backend.migrations.run_all_migrations import MIGRATIONS
        names = [name for name, _ in MIGRATIONS]
        self.assertIn("chat_attachment_schema", names)

    def test_registered_wrapper_actually_calls_the_migration_class(self):
        from backend.migrations import run_all_migrations as run_all_migrations_module
        wrapper = dict(run_all_migrations_module.MIGRATIONS)["chat_attachment_schema"]

        with patch(
            "backend.migrations.chat_attachment_schema_migration.ChatAttachmentSchemaMigration"
        ) as MockMigration:
            wrapper()
            MockMigration.return_value.migrate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
