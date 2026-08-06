"""
Regression tests for a real bug found live during GCP deployment
verification (docs/DEPLOYMENT.md E2E run against the production AuraDB
instance): Contract.document_embedding, Contract.summary_embedding,
Section.embedding, and Clause.embedding were still covered by plain RANGE
indexes (multi_level_embeddings.py, pre-dating P3 item 16's vector
indexes). A RANGE index's property-size limit is smaller than a 1536-dim
float array, so every write to one of these four properties threw
"Property value is too large to index" - confirmed via SHOW INDEXES
against the live instance, which still listed all four as ONLINE RANGE
indexes (two of them, section_embedding/clause_embedding, sitting
uselessly alongside an already-working VECTOR index on the exact same
property - adding the vector index in P3 item 16 never removed the old
broken RANGE index).

Uses the same FakeGraph-records-issued-Cypher pattern as
test_vector_index_search.py.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.migrations.embedding_range_index_fix import (
        EmbeddingRangeIndexFixMigration,
        STALE_RANGE_INDEXES,
    )
    from backend.shared.utils.vector_index_config import VECTOR_INDEXES


class FakeIndexFixGraph:
    def __init__(self):
        self.queries = []

    def query(self, cypher, params=None):
        self.queries.append(cypher)
        return []


class EmbeddingRangeIndexFixMigrationTests(unittest.TestCase):
    def setUp(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            self.migration = EmbeddingRangeIndexFixMigration()
        self.fake_graph = FakeIndexFixGraph()
        self.migration.repository.graph = self.fake_graph

    def test_drops_all_four_stale_range_indexes(self):
        self.migration.migrate()

        drop_queries = [q for q in self.fake_graph.queries if "DROP INDEX" in q]
        self.assertEqual(len(drop_queries), len(STALE_RANGE_INDEXES))
        for index_name, _label, _prop in STALE_RANGE_INDEXES:
            matching = [q for q in drop_queries if index_name in q]
            self.assertEqual(len(matching), 1, f"Missing DROP INDEX for {index_name}")
            self.assertIn("IF EXISTS", matching[0])

    def test_creates_contract_document_and_summary_vector_indexes(self):
        self.migration.migrate()

        create_queries = [q for q in self.fake_graph.queries if "CREATE VECTOR INDEX" in q]

        for index_name in ("contract_document_embedding_vector_index", "contract_summary_embedding_vector_index"):
            self.assertIn(index_name, VECTOR_INDEXES, f"{index_name} must be registered in VECTOR_INDEXES")
            matching = [q for q in create_queries if index_name in q]
            self.assertEqual(len(matching), 1, f"Missing CREATE VECTOR INDEX for {index_name}")
            self.assertIn("cosine", matching[0])

        doc_query = [q for q in create_queries if "contract_document_embedding_vector_index" in q][0]
        self.assertIn("FOR (n:Contract)", doc_query)
        self.assertIn("ON (n.document_embedding)", doc_query)

        summary_query = [q for q in create_queries if "contract_summary_embedding_vector_index" in q][0]
        self.assertIn("FOR (n:Contract)", summary_query)
        self.assertIn("ON (n.summary_embedding)", summary_query)

    def test_drops_happen_and_creates_happen_in_same_migration_run(self):
        # Both halves of the fix must land together - dropping the stale
        # index without ensuring the vector index exists would leave the
        # property entirely unindexed; creating the vector index without
        # dropping the RANGE index would leave the write still broken.
        self.migration.migrate()

        has_drops = any("DROP INDEX" in q for q in self.fake_graph.queries)
        has_creates = any("CREATE VECTOR INDEX" in q for q in self.fake_graph.queries)
        self.assertTrue(has_drops and has_creates)


if __name__ == "__main__":
    unittest.main()
