"""
Regression test: EnhancedPrecedentMatcherTool._find_real_precedents
(backend/agents/enhanced_cuad_tools.py) reads Clause.content directly from
its Cypher result (RETURN cl.content as content) without decrypting it.
P3 item 21 (commit 1395b75) started redacting+encrypting Clause.content at
write time - this read site was missed, so it fed ciphertext into both the
similarity check against the query clause's plaintext (near-guaranteed to
score under the 0.3 relevance threshold against real clause text, since
base64 ciphertext shares no meaningful word overlap with anything) and the
"content" field returned to callers.

Uses the same FakeGraph/tool-repository pattern as
test_precedent_matcher_relationship.py, but stores real
field_encryptor-encrypted content (as real ingestion now does), not plain
text - the existing test's plaintext fixtures wouldn't have caught this,
since field_encryptor.decrypt() gracefully passes plaintext through
unchanged on a failed decrypt, masking the missing decryption step.
"""

import json
import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.enhanced_cuad_tools import EnhancedPrecedentMatcherTool
    from backend.infrastructure.encryption import field_encryptor


class FakeGraph:
    """Same minimal stand-in as test_precedent_matcher_relationship.py, but
    the clause store holds whatever content it's given as-is - the test
    controls whether that's plaintext or real ciphertext."""

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

        return []


class PrecedentMatcherDecryptsClauseContentTests(unittest.TestCase):
    PLAINTEXT = "Either party may terminate this agreement with 30 days written notice."

    def _ingest_encrypted_clause(self, graph, tenant_id, contract_id, clause_type, plaintext_content):
        """Mirrors real ingestion: content is redacted+encrypted before storage
        (enhanced_document_processing_service.py). No PII in this fixture, so
        redact is a no-op - only encryption is exercised here."""
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
                "content": field_encryptor.encrypt(plaintext_content),
            },
        )

    def test_find_real_precedents_returns_decrypted_text_not_ciphertext(self):
        tool = EnhancedPrecedentMatcherTool()
        fake_graph = FakeGraph()
        tool.repository.graph = fake_graph

        self._ingest_encrypted_clause(
            fake_graph, tenant_id="tenant_a", contract_id="CONTRACT_1",
            clause_type="termination for convenience", plaintext_content=self.PLAINTEXT,
        )

        # Sanity check the fixture is actually storing ciphertext, not
        # accidentally plaintext (which would make this test pass for the
        # wrong reason).
        stored = fake_graph.clauses[0]["content"]
        self.assertNotEqual(stored, self.PLAINTEXT)

        precedents = tool._find_real_precedents(
            clause={"clause_type": "termination for convenience",
                    "content": "Party may terminate with notice."},
            tenant_id="tenant_a",
        )

        self.assertEqual(len(precedents), 1)
        self.assertEqual(precedents[0]["content"], self.PLAINTEXT)
        self.assertNotEqual(precedents[0]["content"], stored)

    def test_end_to_end_run_surfaces_decrypted_content_via_similarity_match(self):
        """The similarity check itself operates on decrypted content - a
        real relevance match against ciphertext would essentially never
        clear the 0.3 threshold, so this also proves the fix isn't just
        decrypting after the fact but before the similarity comparison."""
        tool = EnhancedPrecedentMatcherTool()
        fake_graph = FakeGraph()
        tool.repository.graph = fake_graph

        self._ingest_encrypted_clause(
            fake_graph, tenant_id="tenant_a", contract_id="CONTRACT_1",
            clause_type="termination for convenience", plaintext_content=self.PLAINTEXT,
        )

        clauses_input = json.dumps([{
            "clause_type": "termination for convenience",
            "content": "Party may terminate with notice.",
        }])
        result = json.loads(tool._run(clauses_input, tenant_id="tenant_a"))

        self.assertEqual(len(result), 1)
        self.assertGreater(result[0]["precedent_count"], 0)
        self.assertIn("total_precedents", result[0]["trend_analysis"])


if __name__ == "__main__":
    unittest.main()
