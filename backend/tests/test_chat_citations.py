import json
import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.application.services.chat_citation_service import (
        build_validated_citations,
        revalidate_stored_citations,
        _citation_id,
        _legacy_citation_id,
    )
    from backend.application.services.chat_evidence_service import evidence_id


class ChatCitationTests(unittest.TestCase):
    def setUp(self):
        self.graph = MagicMock()
        self.graph.query.return_value = [
            {"contract_id": "CONTRACT_A", "filename": "Clean_MSA.pdf"},
        ]
        self.provenance = patch(
            "backend.application.services.chat_citation_service.enrich_citations_with_provenance",
            side_effect=lambda citations, *_args, **_kwargs: citations,
        )
        self.provenance.start()
        self.addCleanup(self.provenance.stop)

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

    def test_canonical_evidence_id_is_the_citation_identity_and_restores(self):
        item = {
            "source_type": "chunk", "contract_id": "CONTRACT_A",
            "filename": "Clean_MSA.pdf", "facts": {},
            "excerpt": "Payment is due within 90 days.",
            "locator": {"chunk_id": "CHUNK_1", "chunk_index": 3},
            "tool_name": "EnhancedContractSearch", "tool_call_id": "call_1",
            "retrieval_score": 0.9, "verification_status": "tenant_active",
        }
        item["evidence_id"] = evidence_id(item, "tenant_a")
        envelope = {
            "schema_version": "chat-evidence-v1", "tenant_id": "tenant_a",
            "tool_name": "EnhancedContractSearch", "tool_call_id": "call_1",
            "evidence": [item],
        }

        citations = build_validated_citations([envelope], "tenant_a", graph_client=self.graph)
        restored = revalidate_stored_citations(citations, "tenant_a", graph_client=self.graph)

        self.assertEqual(citations[0]["citation_id"], item["evidence_id"])
        self.assertEqual(restored[0]["citation_id"], item["evidence_id"])

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
        active = {"contract_id": "CONTRACT_A", "filename": "Clean_MSA.pdf", "validation_status": "tenant_active"}
        archived = {"contract_id": "ARCHIVED", "filename": "archived.pdf", "validation_status": "tenant_active"}
        stored = [
            {"citation_id": _citation_id(active), **active},
            {"citation_id": _citation_id(archived), **archived},
        ]
        citations = revalidate_stored_citations(stored, "tenant_a", graph_client=self.graph)
        self.assertEqual([citation["citation_id"] for citation in citations], [_citation_id(active)])
        self.assertEqual(citations[0]["filename"], "Clean_MSA.pdf")

    def test_restore_deduplicates_same_saved_source_from_multiple_calls(self):
        source_data = {
            "contract_id": "CONTRACT_A", "filename": "Clean_MSA.pdf",
            "source_type": "chunk", "chunk_id": "CHUNK_1", "excerpt": "Payment terms",
            "validation_status": "tenant_active", "tool_call_id": "call_1",
        }
        source = {"citation_id": _citation_id(source_data), **source_data}
        duplicate_data = {**source_data, "tool_call_id": "call_2"}
        duplicate = {"citation_id": _citation_id(duplicate_data), **duplicate_data}
        citations = revalidate_stored_citations([source, duplicate], "tenant_a", graph_client=self.graph)
        self.assertEqual(len(citations), 1)

    def test_restore_drops_manipulated_source_identity(self):
        source = {
            "contract_id": "CONTRACT_A", "filename": "Clean_MSA.pdf",
            "source_type": "chunk", "chunk_id": "CHUNK_1", "excerpt": "Payment terms",
            "validation_status": "tenant_active",
        }
        stored = {"citation_id": _citation_id(source), **source, "excerpt": "Manipulated"}
        self.assertEqual(
            revalidate_stored_citations([stored], "tenant_a", graph_client=self.graph),
            [],
        )

    def test_restore_accepts_pre_provenance_identity_but_revalidates_it(self):
        source = {
            "contract_id": "CONTRACT_A", "filename": "Clean_MSA.pdf",
            "source_type": "chunk", "chunk_id": "CHUNK_1", "page": None,
            "excerpt": "Payment terms", "validation_status": "tenant_active",
        }
        stored = {"citation_id": _legacy_citation_id(source), **source}
        restored = revalidate_stored_citations([stored], "tenant_a", graph_client=self.graph)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["contract_id"], "CONTRACT_A")


if __name__ == "__main__":
    unittest.main()
