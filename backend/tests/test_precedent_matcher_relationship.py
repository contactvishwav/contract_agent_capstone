"""
Regression test for backend/agents/enhanced_cuad_tools.py:354.

EnhancedPrecedentMatcherTool._find_real_precedents queried a [:CONTAINS]
relationship between Contract and Clause, but real ingestion always creates
[:CONTAINS_CLAUSE] instead (see
backend/application/services/enhanced_document_processing_service.py:258 and
backend/migrations/multi_level_embeddings.py:139). Since [:CONTAINS] is never
created anywhere in the codebase, the query always returned zero rows and
silently fell through to the hardcoded mock-data fallback in
EnhancedPrecedentMatcherTool._run (backend/agents/enhanced_cuad_tools.py:326-338).

This test "ingests" a clause using the same relationship type real ingestion
uses, then runs the real tool end-to-end and asserts it takes the real-
precedents path rather than the mock fallback.
"""

import json
import unittest
from unittest.mock import patch

# Mock Neo4j and Gemini BEFORE importing backend modules that instantiate them
# at module level (backend/shared/utils/contract_search_tool.py:53 builds a
# real Neo4jGraph and calls verify_connectivity() on import). Same pattern as
# backend/tests/test_mcp_capabilities.py.
with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.enhanced_cuad_tools import EnhancedPrecedentMatcherTool


class FakeGraph:
    """
    Minimal in-memory stand-in for Neo4jGraph that only understands the two
    Cypher shapes relevant to this regression: clause ingestion (MERGE ...
    [:CONTAINS_CLAUSE]) and the precedent-search MATCH used by
    EnhancedPrecedentMatcherTool._find_real_precedents. A query for the old,
    incorrect [:CONTAINS] relationship structurally can never match anything
    here, mirroring a real Neo4j graph where that relationship type is never
    created.
    """

    def __init__(self):
        self.clauses = []

    def query(self, cypher: str, params: dict = None):
        params = params or {}

        if "MERGE (cl:Clause" in cypher and "CONTAINS_CLAUSE" in cypher:
            self.clauses.append({
                "contract_id": params["file_id"],
                "tenant_id": params["tenant_id"],
                "clause_type": params["clause_type"],
                "content": params["content"],
            })
            return [{"clause_id": params.get("clause_id")}]

        if "[:CONTAINS_CLAUSE]->(cl:Clause)" in cypher:
            tenant_id = params["tenant_id"]
            clause_type = params["clause_type"]
            return [
                {
                    "type": c["clause_type"],
                    "content": c["content"],
                    "risk_level": "LOW",
                    "contract_risk": 20,
                    "contract_id": c["contract_id"],
                    "contract_type": "MSA",
                }
                for c in self.clauses
                if c["tenant_id"] == tenant_id and clause_type in c["clause_type"].lower()
            ]

        if "[:CONTAINS]->(cl:Clause)" in cypher:
            # The pre-fix relationship type. Real ingestion never creates a
            # bare [:CONTAINS] edge, so a real graph would always return [].
            return []

        return []


class TestPrecedentSearchRelationshipType(unittest.TestCase):
    def _ingest_sample_clause(self, graph, tenant_id, contract_id, clause_type, content):
        """Mirrors the real ingestion write in enhanced_document_processing_service.py:246-258."""
        graph.query(
            """
            MATCH (c:Contract {file_id: $file_id})
            MERGE (cl:Clause {id: $clause_id})
            SET cl.clause_type = $clause_type,
                cl.content = $content
            MERGE (c)-[:CONTAINS_CLAUSE]->(cl)
            """,
            {
                "file_id": contract_id,
                "clause_id": f"{contract_id}_clause_0",
                "tenant_id": tenant_id,
                "clause_type": clause_type,
                "content": content,
            },
        )

    def test_precedent_search_finds_real_ingested_clause(self):
        tool = EnhancedPrecedentMatcherTool()
        fake_graph = FakeGraph()
        tool.repository.graph = fake_graph

        self._ingest_sample_clause(
            fake_graph,
            tenant_id="tenant_a",
            contract_id="CONTRACT_1",
            clause_type="termination for convenience",
            content="Either party may terminate this agreement with 30 days written notice.",
        )

        clauses_input = json.dumps([{
            "clause_type": "termination for convenience",
            "content": "Party may terminate with notice.",
        }])
        result = json.loads(tool._run(clauses_input, tenant_id="tenant_a"))

        self.assertEqual(len(result), 1)
        match = result[0]
        self.assertGreater(match["precedent_count"], 0)
        # Only the real-precedents path populates trend_analysis with these
        # keys; the mock-data fallback returns {"note": "Limited historical
        # data available"} instead. Their presence confirms we took the real
        # path, not the fallback.
        self.assertIn("total_precedents", match["trend_analysis"])
        self.assertEqual(match["trend_analysis"]["total_precedents"], 1)

    def test_precedent_search_isolates_by_tenant(self):
        tool = EnhancedPrecedentMatcherTool()
        fake_graph = FakeGraph()
        tool.repository.graph = fake_graph

        self._ingest_sample_clause(
            fake_graph,
            tenant_id="tenant_a",
            contract_id="CONTRACT_1",
            clause_type="termination for convenience",
            content="Either party may terminate this agreement with 30 days written notice.",
        )

        clauses_input = json.dumps([{
            "clause_type": "termination for convenience",
            "content": "Party may terminate with notice.",
        }])
        # Same clause type, but a different tenant must not see tenant_a's clause.
        result = json.loads(tool._run(clauses_input, tenant_id="tenant_b"))

        for match in result:
            self.assertNotIn("CONTRACT_1", json.dumps(match))


if __name__ == "__main__":
    unittest.main()
