"""
Regression tests for P3 item 16: brute-force gds.similarity.cosine()/
vector.similarity.cosine() scans (13 + 4 call sites, scoring every node of
a label on every search) replaced with real Neo4j vector indexes
(CREATE VECTOR INDEX + db.index.vector.queryNodes) for every node-property
embedding (Contract, Section, Clause, Chunk, PolicyDocument). The two
PARTY_TO relationship-embedding call sites are intentionally left as
brute-force (relationship vector indexes are a newer, less universally
supported feature, and the actual connection target is an externally
managed shared instance whose edition isn't controlled by this migration).

Uses a FakeGraph that records every issued Cypher string, so these tests
assert on the real query text produced by application code, not just a
migration script in isolation.
"""

import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.migrations.vector_index_migration import VectorIndexMigration
    from backend.shared.utils.vector_index_config import VECTOR_INDEXES, EMBEDDING_DIMENSIONS
    import backend.shared.utils.search_strategies as search_strategies
    import backend.shared.utils.contract_search_tool as contract_search_tool
    from backend.infrastructure.policy_repository import PolicyRepository
    from backend.domain.search_entities import SearchParams, SearchLevel


class FakeVectorGraph:
    """Records every issued query; returns an empty result list (these
    tests check the Cypher text sent, not row-level result shaping)."""

    def __init__(self):
        self.queries = []

    def query(self, cypher, params=None):
        self.queries.append((cypher, params or {}))
        return []


class FakeEmbeddingService:
    def embed_query(self, text):
        return [0.1] * EMBEDDING_DIMENSIONS


class VectorIndexMigrationTests(unittest.TestCase):
    def test_creates_vector_index_for_every_embedding_label(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            migration = VectorIndexMigration()
        fake_graph = FakeVectorGraph()
        migration.repository.graph = fake_graph

        migration.migrate()

        issued = [q for q, _ in fake_graph.queries]
        self.assertEqual(len(issued), len(VECTOR_INDEXES))
        for index_name, (label, prop) in VECTOR_INDEXES.items():
            matching = [q for q in issued if index_name in q]
            self.assertEqual(len(matching), 1, f"Missing CREATE VECTOR INDEX for {index_name}")
            self.assertIn("CREATE VECTOR INDEX", matching[0])
            self.assertIn(f"FOR (n:{label})", matching[0])
            self.assertIn(f"ON (n.{prop})", matching[0])
            self.assertIn(str(EMBEDDING_DIMENSIONS), matching[0])
            self.assertIn("cosine", matching[0])


class DocumentSearchStrategyVectorTests(unittest.TestCase):
    def test_semantic_search_uses_vector_index_not_brute_force(self):
        fake_graph = FakeVectorGraph()
        with patch.object(search_strategies, "graph", fake_graph), \
             patch.object(search_strategies, "embedding", FakeEmbeddingService()):
            strategy = search_strategies.DocumentSearchStrategy()
            strategy.execute(SearchParams(
                search_level=SearchLevel.DOCUMENT, tenant_id="tenant_1", query="governing law clause"
            ))

        cypher, params = fake_graph.queries[0]
        self.assertIn("db.index.vector.queryNodes", cypher)
        self.assertIn("contract_embedding_vector_index", cypher)
        self.assertNotIn("vector.similarity.cosine", cypher)
        self.assertIn("k", params)
        # Real, previously-absent tenant scoping (SearchParams had no
        # tenant_id field at all until this fix - this endpoint returned
        # every tenant's contracts to any authenticated caller).
        self.assertIn("c.tenant_id = $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_1")


class ClauseSearchStrategyVectorTests(unittest.TestCase):
    def test_semantic_search_uses_vector_index_not_brute_force(self):
        fake_graph = FakeVectorGraph()
        with patch.object(search_strategies, "graph", fake_graph), \
             patch.object(search_strategies, "embedding", FakeEmbeddingService()):
            strategy = search_strategies.ClauseSearchStrategy()
            strategy.execute(SearchParams(
                search_level=SearchLevel.CLAUSE, tenant_id="tenant_1", query="liability cap"
            ))

        cypher, params = fake_graph.queries[0]
        self.assertIn("db.index.vector.queryNodes", cypher)
        self.assertIn("clause_embedding_vector_index", cypher)
        self.assertNotIn("vector.similarity.cosine", cypher)
        self.assertIn("c.tenant_id = $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_1")

    def test_clause_content_is_decrypted_before_returning(self):
        """cl.content is encrypted at rest (clause_repository.py) - this
        strategy's raw Cypher read must decrypt it, not return ciphertext
        as if it were the real clause text."""
        from backend.infrastructure.encryption import field_encryptor

        class FakeGraphWithEncryptedClause:
            def query(self, cypher, params=None):
                return [{"result": {
                    "total_count": 1,
                    "clauses": [{
                        "contract_id": "c1", "clause_type": "Governing Law",
                        "content": field_encryptor.encrypt("This Agreement is governed by Delaware law."),
                        "confidence": 0.9,
                    }],
                }}]

        with patch.object(search_strategies, "graph", FakeGraphWithEncryptedClause()), \
             patch.object(search_strategies, "embedding", FakeEmbeddingService()):
            strategy = search_strategies.ClauseSearchStrategy()
            result = strategy.execute(SearchParams(
                search_level=SearchLevel.CLAUSE, tenant_id="tenant_1", query="governing law"
            ))

        self.assertEqual(result.items[0]["content"], "This Agreement is governed by Delaware law.")


class SectionSearchStrategyVectorTests(unittest.TestCase):
    def test_semantic_search_uses_vector_index_not_brute_force(self):
        fake_graph = FakeVectorGraph()
        with patch.object(search_strategies, "graph", fake_graph), \
             patch.object(search_strategies, "embedding", FakeEmbeddingService()):
            strategy = search_strategies.SectionSearchStrategy()
            strategy.execute(SearchParams(
                search_level=SearchLevel.SECTION, tenant_id="tenant_1", query="termination"
            ))

        cypher, params = fake_graph.queries[0]
        self.assertIn("db.index.vector.queryNodes", cypher)
        self.assertIn("section_embedding_vector_index", cypher)
        self.assertNotIn("vector.similarity.cosine", cypher)
        self.assertIn("c.tenant_id = $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_1")


class RelationshipSearchStrategyVectorTests(unittest.TestCase):
    def test_semantic_search_scopes_by_tenant(self):
        fake_graph = FakeVectorGraph()
        with patch.object(search_strategies, "graph", fake_graph), \
             patch.object(search_strategies, "embedding", FakeEmbeddingService()):
            strategy = search_strategies.RelationshipSearchStrategy()
            strategy.execute(SearchParams(
                search_level=SearchLevel.RELATIONSHIP, tenant_id="tenant_1", query="acme corp"
            ))

        cypher, params = fake_graph.queries[0]
        self.assertIn("c.tenant_id = $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_1")

    def test_non_semantic_search_also_scopes_by_tenant(self):
        """No query text at all - the WHERE clause is built from the
        elif-filters branch, a separate code path from the query branch
        above that needs its own proof tenant_id survived."""
        fake_graph = FakeVectorGraph()
        with patch.object(search_strategies, "graph", fake_graph), \
             patch.object(search_strategies, "embedding", FakeEmbeddingService()):
            strategy = search_strategies.RelationshipSearchStrategy()
            strategy.execute(SearchParams(
                search_level=SearchLevel.RELATIONSHIP, tenant_id="tenant_1", parties=["Acme Corp"]
            ))

        cypher, params = fake_graph.queries[0]
        self.assertIn("c.tenant_id = $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_1")


class ContractSearchToolVectorTests(unittest.TestCase):
    def test_summary_search_uses_vector_index_and_still_scopes_by_tenant(self):
        fake_graph = FakeVectorGraph()
        with patch.object(contract_search_tool, "graph", fake_graph):
            contract_search_tool.get_contracts(
                embeddings=FakeEmbeddingService(),
                tenant_id="tenant_1",
                summary_search="exclusivity clause",
            )

        cypher, params = fake_graph.queries[0]
        self.assertIn("db.index.vector.queryNodes", cypher)
        self.assertIn("contract_embedding_vector_index", cypher)
        self.assertNotIn("vector.similarity.cosine", cypher)
        # Tenant scoping must survive the switch to indexed search - applied
        # as a post-filter after the vector index YIELD, not lost.
        self.assertIn("c.tenant_id = $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_1")


class PolicyRepositoryVectorTests(unittest.TestCase):
    def test_semantic_policy_search_uses_vector_index_and_scopes_by_tenant(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            repo = PolicyRepository()
        fake_graph = FakeVectorGraph()
        repo.graph = fake_graph
        repo.embedding_service = FakeEmbeddingService()

        repo.search_policies_semantic("liability", "tenant_acme", limit=5)

        cypher, params = fake_graph.queries[0]
        self.assertIn("db.index.vector.queryNodes", cypher)
        self.assertIn("policy_document_embedding_vector_index", cypher)
        self.assertNotIn("gds.similarity.cosine", cypher)
        self.assertIn("p.tenant_id = $tenant_id", cypher)
        self.assertEqual(params["tenant_id"], "tenant_acme")


if __name__ == "__main__":
    unittest.main()
