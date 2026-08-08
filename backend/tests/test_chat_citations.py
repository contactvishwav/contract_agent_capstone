import json
import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.application.services.chat_citation_service import (
        build_validated_citations,
        revalidate_stored_citations,
    )


class ChatCitationTests(unittest.TestCase):
    def setUp(self):
        self.graph = MagicMock()
        self.graph.query.return_value = [
            {"contract_id": "CONTRACT_A", "filename": "Clean_MSA.pdf"},
        ]

    def test_derives_source_only_from_tool_evidence_and_validates_tenant(self):
        evidence = [{
            "tool_name": "EnhancedContractSearch",
            "tool_call_id": "call_1",
            "content": json.dumps({"result": {"chunks": [
                {"contract_id": "CONTRACT_A", "filename": "untrusted.pdf", "chunk_id": "CHUNK_1",
                 "chunk_index": 3, "content": "Payment is due within 90 days.", "start_offset": 10, "end_offset": 40},
                {"contract_id": "OTHER_TENANT", "chunk_id": "CHUNK_2", "content": "Do not expose"},
            ]}}),
        }]

        citations = build_validated_citations(evidence, "tenant_a", graph_client=self.graph)

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["contract_id"], "CONTRACT_A")
        self.assertEqual(citations[0]["filename"], "Clean_MSA.pdf")
        self.assertEqual(citations[0]["source_type"], "chunk")
        self.assertIsNone(citations[0]["page"])
        self.assertEqual(citations[0]["validation_status"], "tenant_active")
        self.graph.query.assert_called_once()
        self.assertEqual(self.graph.query.call_args.args[1]["tenant_id"], "tenant_a")

    def test_plain_model_text_cannot_fabricate_a_citation(self):
        citations = build_validated_citations(
            [{"content": "The answer cites CONTRACT_A but this is not structured tool evidence."}],
            "tenant_a",
            graph_client=self.graph,
        )
        self.assertEqual(citations, [])
        self.graph.query.assert_not_called()

    def test_same_source_from_multiple_tool_calls_is_deduplicated(self):
        content = json.dumps({"chunks": [{
            "contract_id": "CONTRACT_A", "chunk_id": "CHUNK_1", "chunk_index": 3,
            "content": "Payment is due within 90 days.",
        }]})
        citations = build_validated_citations(
            [
                {"content": content, "tool_call_id": "call_1", "tool_name": "EnhancedContractSearch"},
                {"content": content, "tool_call_id": "call_2", "tool_name": "EnhancedContractSearch"},
            ],
            "tenant_a",
            graph_client=self.graph,
        )
        self.assertEqual(len(citations), 1)

    def test_restore_drops_archived_or_cross_tenant_sources(self):
        stored = [
            {"citation_id": "CIT_A", "contract_id": "CONTRACT_A", "filename": "old-name.pdf", "validation_status": "tenant_active"},
            {"citation_id": "CIT_B", "contract_id": "ARCHIVED", "filename": "archived.pdf", "validation_status": "tenant_active"},
        ]
        citations = revalidate_stored_citations(stored, "tenant_a", graph_client=self.graph)
        self.assertEqual([citation["citation_id"] for citation in citations], ["CIT_A"])
        self.assertEqual(citations[0]["filename"], "Clean_MSA.pdf")

    def test_restore_deduplicates_same_saved_source_from_multiple_calls(self):
        source = {
            "citation_id": "CIT_A", "contract_id": "CONTRACT_A", "filename": "Clean_MSA.pdf",
            "source_type": "chunk", "chunk_id": "CHUNK_1", "excerpt": "Payment terms",
            "validation_status": "tenant_active", "tool_call_id": "call_1",
        }
        duplicate = {**source, "citation_id": "CIT_B", "tool_call_id": "call_2"}
        citations = revalidate_stored_citations([source, duplicate], "tenant_a", graph_client=self.graph)
        self.assertEqual(len(citations), 1)


if __name__ == "__main__":
    unittest.main()
