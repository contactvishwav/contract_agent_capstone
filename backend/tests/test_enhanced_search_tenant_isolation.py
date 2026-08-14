"""
Regression test for a real, live, cross-tenant data leak found while
building re-ranking for this search path: `POST /api/contracts/search/
enhanced` (and its /search/clauses, /search/sections, /search/relationships
siblings) had zero tenant isolation. `SearchParams` had no `tenant_id`
field at all, and the route only used `requires_permission(...)` as a bare
`dependencies=[...]` gate - the resolved identity was never captured, so
tenant_id never reached any of the four search strategies' Cypher queries.
Any authenticated caller (any role, any tenant - VIEWER included, since
`Permission.ANALYZE` is held by every role) could search and read every
other tenant's contracts, clauses, sections, and party relationships.

test_vector_index_search.py already proves this at the Cypher-text/params
level for all four strategies in isolation. This file proves it the other
way - through the real FastAPI route, real JWT identity resolution, and a
fake graph that actually enforces tenant filtering (unlike a bare
MagicMock, which would return the same canned data regardless of what
tenant_id the query carried, making a cross-tenant leak invisible to the
test) - the same "prove it can't happen even against a dishonest caller"
discipline already established for every other tenant-isolation test in
this suite (test_jwt_auth.py's RealCrossTenantIsolationViaTokenTests,
test_debug_routes_tenant_isolation.py).
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api.enhanced_contract_search import router as search_router
    import backend.shared.utils.search_strategies as search_strategies

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tests.conftest import auth_headers


class FakeTenantScopedGraph:
    """
    Mirrors real Neo4j tenant-filtered behavior: only returns a contract if
    the query's tenant_id param matches the contract's real owning tenant -
    a bare MagicMock would return the same canned result regardless of
    tenant_id, which would make a cross-tenant leak invisible to this test.
    """

    def __init__(self):
        self.queries = []
        self._data = {
            # relevance_score present (dynamic_retrieval.py's score-delta
            # filter runs on real query results and drops anything without
            # a numeric score - see DocumentSearchStrategy.execute).
            "tenant_a": {"file_id": "contract_a", "summary": "Tenant A's confidential MSA", "relevance_score": 0.9},
            "tenant_b": {"file_id": "contract_b", "summary": "Tenant B's confidential NDA", "relevance_score": 0.9},
        }

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        tenant_id = params.get("tenant_id")
        contract = self._data.get(tenant_id)
        contracts = [contract] if contract else []
        return [{"result": {"total_count": len(contracts), "contracts": contracts}}]


class FakeEmbeddingService:
    def embed_query(self, text):
        return [0.1] * 1536


class EnhancedSearchTenantIsolationTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(search_router, prefix="/api")  # router itself already carries /contracts
        self.client = TestClient(app)
        self.fake_graph = FakeTenantScopedGraph()
        self._graph_patch = patch.object(search_strategies, "graph", self.fake_graph)
        self._embedding_patch = patch.object(search_strategies, "embedding", FakeEmbeddingService())
        self._graph_patch.start()
        self._embedding_patch.start()
        self.addCleanup(self._graph_patch.stop)
        self.addCleanup(self._embedding_patch.stop)

    def _search(self, tenant_id, role="ADMIN"):
        return self.client.post(
            "/api/contracts/search/enhanced",
            json={"search_level": "document", "query": "confidential agreement"},
            headers=auth_headers(tenant_id=tenant_id, role=role),
        )

    def test_tenant_a_sees_only_its_own_contract(self):
        response = self._search("tenant_a")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["contracts_found"], 1)
        documents = body["results"][0]["documents"]
        self.assertEqual(documents[0]["file_id"], "contract_a")

    def test_tenant_b_never_sees_tenant_as_contract(self):
        response = self._search("tenant_b")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["contracts_found"], 1)
        documents = body["results"][0]["documents"]
        self.assertEqual(documents[0]["file_id"], "contract_b")
        self.assertNotIn("contract_a", [d["file_id"] for d in documents])

    def test_query_actually_carried_the_callers_own_tenant_id_not_a_default(self):
        """The real proof: the Cypher params sent to the graph reflect the
        JWT's tenant_id, not a hardcoded/omitted value - a caller cannot
        get another tenant's data no matter what (nothing in the request
        body even has a tenant_id field to try smuggling one through)."""
        self._search("tenant_a")
        _, params = self.fake_graph.queries[-1]
        self.assertEqual(params.get("tenant_id"), "tenant_a")

        self._search("tenant_b")
        _, params = self.fake_graph.queries[-1]
        self.assertEqual(params.get("tenant_id"), "tenant_b")

    def test_viewer_role_can_still_leak_without_the_fix_so_this_covers_the_real_exposure(self):
        """Permission.ANALYZE (the only gate on this route) is held by
        every role including VIEWER - this isn't an admin-only exposure."""
        response = self._search("tenant_a", role="VIEWER")
        self.assertEqual(response.status_code, 200)
        documents = response.json()["results"][0]["documents"]
        self.assertEqual(documents[0]["file_id"], "contract_a")


if __name__ == "__main__":
    unittest.main()
