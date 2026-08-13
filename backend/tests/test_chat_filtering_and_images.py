import unittest
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from backend.contract_chat_agent import _extract_prompt_filters, _forced_evidence_args, _message_text, _conversation_has_image_evidence
from backend.shared.utils.enhanced_contract_search_tool import _search_documents, SearchLevel

class TestContractChatFilteringAndImages(unittest.TestCase):

    def test_extract_prompt_filters(self):
        """Verify prompt filter extraction maps SOW, MSA, NDA correctly"""
        sow_args = _extract_prompt_filters("How many SOW contracts?")
        self.assertEqual(sow_args.get("contract_type"), "SOW")
        self.assertEqual(sow_args.get("search_level"), "document")

        msa_args = _extract_prompt_filters("List all MSA contracts available")
        self.assertEqual(msa_args.get("contract_type"), "MSA")

        nda_args = _extract_prompt_filters("Count of non-disclosure agreements")
        self.assertEqual(nda_args.get("contract_type"), "NDA")

        plain_args = _extract_prompt_filters("How many contracts?")
        self.assertNotIn("contract_type", plain_args)

    def test_message_text_extraction(self):
        """Verify image attachment blocks are stripped from text extraction"""
        plain_text = "What is this contract about?"
        self.assertEqual(_message_text(plain_text), plain_text)

        multimodal_content = [
            {"type": "text", "text": "Describe this attachment"},
            {"type": "image", "image_url": "data:image/png;base64,iVBORw0KGgoAAAANSU..."}
        ]
        extracted = _message_text(multimodal_content)
        self.assertEqual(extracted, "Describe this attachment")

    def test_conversation_has_image_evidence(self):
        """Verify image evidence detection across multi-turn messages"""
        messages_without_image = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there!"),
            HumanMessage(content="How many SOW contracts?")
        ]
        self.assertFalse(_conversation_has_image_evidence(messages_without_image))

        messages_with_image = [
            HumanMessage(content=[
                {"type": "text", "text": "Check this clause"},
                {"type": "image", "image_url": "data:image/png;base64,1234"}
            ]),
            AIMessage(content="I see the clause image."),
            HumanMessage(content="What does it specify?")
        ]
        self.assertTrue(_conversation_has_image_evidence(messages_with_image))

    @patch("backend.shared.utils.enhanced_contract_search_tool.graph.query")
    def test_search_documents_contract_type_filtering(self, mock_graph_query):
        """Verify _search_documents appends contract_type Cypher filter correctly"""
        mock_graph_query.return_value = [{"result": {"total_count": 1, "contracts": [{"file_id": "SOW_1", "filename": "Clean_SOW.pdf", "contract_type": "SOW"}]}}]
        
        embeddings = MagicMock()
        filters = ["c.tenant_id = $tenant_id"]
        params = {"tenant_id": "test_tenant"}

        res = _search_documents(
            embeddings=embeddings,
            tenant_id="test_tenant",
            summary_search=None,
            filters=filters,
            params=params,
            min_effective_date=None,
            max_effective_date=None,
            min_end_date=None,
            max_end_date=None,
            contract_type="SOW",
            parties=None,
            active=True,
            cypher_aggregation=None,
            monetary_value=None,
            governing_law=None
        )

        self.assertTrue(mock_graph_query.called)
        cypher_arg = mock_graph_query.call_args[0][0]
        params_arg = mock_graph_query.call_args[0][1]

        self.assertIn("toUpper(c.contract_type) CONTAINS toUpper($contract_type)", cypher_arg)
        self.assertEqual(params_arg.get("contract_type"), "SOW")

if __name__ == "__main__":
    unittest.main()
