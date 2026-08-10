"""
Real, confirmed bug found live during ADR-008 Stage 2 verification:
contract_chat_agent.py's assistant node built `latest_prompt` via bare
`str(message.content)`. That's safe when content is a plain string (the
only shape that existed before image attachments), but a HumanMessage's
content is a list of content blocks (text + image) when the turn carries
an attachment (main.py's _build_prompt_message) - str() on that list
produces its Python repr, INCLUDING the full raw base64 image data, which
then flowed into _forced_evidence_args() as a search term. Live-reproduced:
a real GPT-4o vision turn forced an EnhancedContractSearch call with
`summary_search` containing the literal base64 blob, retrieved nothing
relevant, and Output Guard correctly (from its own perspective) rejected
the resulting ungrounded answer - an otherwise perfectly answerable vision
question failing for a completely unrelated plumbing reason.

Fixed with _message_text(), which extracts only the text block(s) from a
list-shaped content, mirroring main.py's _normalize_ai_message_content's
established flattening approach (a local copy, not a shared import -
contract_chat_agent.py must not import from main.py, which already imports
contract_chat_agent.py).
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.contract_chat_agent import _message_text, get_agent

from langchain_core.messages import AIMessage, HumanMessage


class MessageTextExtractionTests(unittest.TestCase):
    def test_plain_string_content_passes_through_unchanged(self):
        self.assertEqual(_message_text("What are the payment terms?"), "What are the payment terms?")

    def test_list_content_extracts_only_the_text_block(self):
        content = [
            {"type": "text", "text": "Describe this image."},
            {"type": "image", "base64": "aGVsbG8=" * 1000, "mime_type": "image/png"},
        ]
        self.assertEqual(_message_text(content), "Describe this image.")

    def test_list_content_never_includes_base64_image_data(self):
        long_base64 = "Q" * 5000
        content = [
            {"type": "text", "text": "hi"},
            {"type": "image", "base64": long_base64, "mime_type": "image/png"},
        ]
        self.assertNotIn(long_base64, _message_text(content))
        self.assertNotIn("base64", _message_text(content))

    def test_multiple_text_blocks_are_joined(self):
        content = [{"type": "text", "text": "part one. "}, {"type": "text", "text": "part two."}]
        self.assertEqual(_message_text(content), "part one. part two.")

    def test_image_only_content_with_no_text_block_returns_empty_string(self):
        content = [{"type": "image", "base64": "abc", "mime_type": "image/png"}]
        self.assertEqual(_message_text(content), "")

    def test_falsy_content_returns_empty_string(self):
        self.assertEqual(_message_text(None), "")
        self.assertEqual(_message_text(""), "")


class ForcedEvidenceRoutingWithImageContentTests(unittest.TestCase):
    """End-to-end through the real graph node (get_agent), matching
    test_chat_contract_selection.py's pattern - proves the actual bug
    scenario is fixed, not just the helper in isolation."""

    def test_image_bearing_turn_never_leaks_base64_into_the_forced_search_term(self):
        fake_llm = MagicMock()
        fake_llm.bind_tools.return_value = fake_llm
        # No tool_calls -> triggers the exact forced-evidence path the live
        # bug was found in.
        fake_llm.invoke.return_value = AIMessage(content="I can see a blue circle and a red square.")

        long_base64 = "Q" * 5000
        image_message = HumanMessage(content=[
            {"type": "text", "text": "Describe the shapes in this image."},
            {"type": "image", "base64": long_base64, "mime_type": "image/png"},
        ])

        with patch(
            "backend.shared.utils.enhanced_contract_search_tool.EnhancedContractSearchTool._run",
            return_value={"result": {"total_count": 0, "contracts": []}},
        ) as fake_run, patch(
            "backend.contract_chat_agent.build_evidence_envelope",
            return_value={
                "schema_version": "chat-evidence-v1", "tenant_id": "tenant_a",
                "tool_name": "EnhancedContractSearch", "tool_call_id": "forced_test", "evidence": [],
            },
        ):
            graph = get_agent(fake_llm)
            graph.invoke(
                {"messages": [image_message]},
                config={"configurable": {"tenant_id": "tenant_a"}},
            )

        self.assertEqual(fake_run.call_count, 1)
        _, kwargs = fake_run.call_args
        search_term = kwargs.get("summary_search") or ""
        self.assertNotIn(long_base64, search_term, "the raw base64 image data must never reach a search term")
        self.assertNotIn("base64", search_term)
        self.assertIn("Describe the shapes", search_term)


if __name__ == "__main__":
    unittest.main()
