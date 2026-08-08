"""
Contract Chat functional audit, item 1: search_level silently defaulted
to SearchLevel.DOCUMENT everywhere (the schema Field default, _run's own
parameter default, and get_contracts_multi_level's own parameter
default), and the field's description never told the model 'chunk' was
even an option. 'document' only searches each contract's short AI-
generated summary paragraph - never the real contract text - so a real
question containing a section title verbatim from the document
("Fees & Invoicing") returned total_count: 0, confirmed live against
real production data, because the model's tool call omitted
search_level entirely and it silently fell through to the one level
guaranteed not to find real content.

Fixed by defaulting to SearchLevel.ALL everywhere (already proven to
aggregate every level, chunks included, in one call) and rewriting the
field/tool descriptions to explain what each level actually searches.
This closes the whole class of "picked/defaulted to the wrong level"
false negatives, not just the one reproduced phrasing.
"""

import unittest
from unittest.mock import MagicMock, patch


def _search_tool_module():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        from backend.shared.utils import enhanced_contract_search_tool
    return enhanced_contract_search_tool


class SearchLevelDefaultTests(unittest.TestCase):
    """Every place search_level can default now defaults to ALL, not
    DOCUMENT - checked at all three defaulting sites independently,
    since the real bug was that they'd drifted/could drift out of sync."""

    def test_input_schema_defaults_to_all(self):
        tool_module = _search_tool_module()
        schema_instance = tool_module.EnhancedContractInput()
        self.assertEqual(schema_instance.search_level, tool_module.SearchLevel.ALL)

    def test_get_contracts_multi_level_defaults_to_all(self):
        import inspect
        tool_module = _search_tool_module()
        sig = inspect.signature(tool_module.get_contracts_multi_level)
        self.assertEqual(sig.parameters["search_level"].default, tool_module.SearchLevel.ALL)

    def test_tool_run_defaults_to_all(self):
        import inspect
        tool_module = _search_tool_module()
        sig = inspect.signature(tool_module.EnhancedContractSearchTool._run)
        self.assertEqual(sig.parameters["search_level"].default, tool_module.SearchLevel.ALL)

    def test_search_level_description_mentions_chunk_and_explains_document_limitation(self):
        """The real, confirmed second half of the bug: even with a good
        default, a model that explicitly picks a level needs real
        guidance - the old description just echoed the enum names and
        never mentioned 'chunk' as an option at all."""
        tool_module = _search_tool_module()
        field_info = tool_module.EnhancedContractInput.model_fields["search_level"]
        description = (field_info.description or "").lower()
        self.assertIn("chunk", description)
        self.assertIn("summary", description)


class ExactSectionTitleFindsRealChunkContentTests(unittest.TestCase):
    """Regression for the literal reproduced case: a query containing an
    exact verbatim section title from the document ('Fees & Invoicing')
    must find it - proven here the same way the live audit proved it,
    by simulating a model tool call that omits search_level entirely
    (exactly what the real model did) and confirming the resulting
    default (ALL) still surfaces the real chunk match, even though the
    document-level (summary) search alone finds nothing for it."""

    class _LevelAwareFakeGraph:
        """Returns a real chunk hit for chunk-level queries and an
        empty result for every other level - mirrors the real
        production shape (short AI summary doesn't mention the section
        title; the real chunk text does)."""

        def __init__(self):
            self.queries = []

        def query(self, cypher, params=None):
            params = params or {}
            self.queries.append((cypher, params))
            if "chunk_embedding_vector_index" in cypher:
                return [{
                    "document_id": "UPLOADED_TEST", "chunk_type": "section_part",
                    "content": self._encrypt("4. Fees & Invoicing\nTotal project fee: $500,000."),
                    "chunk_index": 0, "quality_score": 0.9, "similarity_score": 0.81,
                }]
            return []

        @staticmethod
        def _encrypt(text):
            from backend.infrastructure.encryption import field_encryptor
            return field_encryptor.encrypt(text)

    def test_omitting_search_level_still_finds_the_exact_section_title(self):
        tool_module = _search_tool_module()
        fake_graph = self._LevelAwareFakeGraph()
        fake_embeddings = MagicMock()
        fake_embeddings.embed_query.return_value = [0.1, 0.2, 0.3]

        with patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False), \
             patch.object(tool_module, "graph", fake_graph):
            # No search_level kwarg at all - exactly what the real model's
            # tool call looked like live: {"summary_search": "Fees & Invoicing"}
            result = tool_module.get_contracts_multi_level(
                fake_embeddings, tenant_id="tenant_a", summary_search="Fees & Invoicing",
            )

        self.assertIn("chunks", result[0], "omitting search_level must still search chunk-level content")
        chunks = result[0]["chunks"][0]["result"]["chunks"]
        self.assertGreaterEqual(len(chunks), 1, "the real section title text must be found")
        self.assertIn("Fees & Invoicing", chunks[0]["content"])


if __name__ == "__main__":
    unittest.main()
