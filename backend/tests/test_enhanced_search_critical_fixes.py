"""
Regression unit tests for Enhanced Search CRITICAL items 1, 2, and 3:
1. All Levels search key mapping (plural keys: documents, sections, clauses, relationships).
2. Clause storage encryption at rest in EnhancedDocumentProcessingService.
3. SearchResponseMapper error handling (success=False, error surfaced).
"""

import unittest
from unittest.mock import MagicMock, patch
from backend.domain.search_entities import SearchLevel, SearchParams, SearchResult
from backend.application.services.enhanced_search_service import EnhancedSearchService
from backend.shared.utils.search_mapper import SearchResponseMapper
from backend.infrastructure.encryption import field_encryptor


class EnhancedSearchCriticalFixesTests(unittest.TestCase):

    def test_search_all_levels_returns_plural_keys(self):
        """Item 2: _search_all_levels must use plural keys matching frontend and mapper"""
        service = EnhancedSearchService()
        
        # Mock individual level strategies to return dummy SearchResults
        for level, strategy in service._strategies.items():
            if level != SearchLevel.ALL:
                mock_strategy = MagicMock()
                mock_strategy.execute.return_value = SearchResult(
                    total_count=1,
                    items=[{"test_id": f"{level.value}-1"}],
                    search_metadata={"search_level": level.value}
                )
                service._strategies[level] = mock_strategy

        params = SearchParams(search_level=SearchLevel.ALL, tenant_id="tenant-123", query="test query")
        result = service.search(params)

        self.assertEqual(result.total_count, 4)
        self.assertEqual(len(result.items), 1)
        all_results = result.items[0]

        # Assert plural keys exist and singular keys do NOT exist
        self.assertIn("documents", all_results)
        self.assertIn("sections", all_results)
        self.assertIn("clauses", all_results)
        self.assertIn("relationships", all_results)

        self.assertNotIn("document", all_results)
        self.assertNotIn("section", all_results)
        self.assertNotIn("clause", all_results)
        self.assertNotIn("relationship", all_results)

        # Test SearchResponseMapper with "all" search level
        api_response = SearchResponseMapper.to_api_response(result, "all")
        self.assertTrue(api_response["success"])
        self.assertEqual(api_response["contracts_found"], 4)
        self.assertEqual(api_response["results"], [all_results])

    def test_search_mapper_surfaces_backend_errors(self):
        """Item 3: SearchResponseMapper sets success=False and error when search_metadata has error"""
        error_result = SearchResult(
            total_count=0,
            items=[],
            search_metadata={"search_level": "clause", "error": "Decryption error: invalid ciphertext"}
        )

        api_response = SearchResponseMapper.to_api_response(error_result, "clause")

        self.assertFalse(api_response["success"])
        self.assertEqual(api_response["error"], "Decryption error: invalid ciphertext")
        self.assertIn("Search error occurred: Decryption error", api_response["message"])

    def test_enhanced_document_processing_service_encrypts_clause_content(self):
        """Item 3: Clause storage in EnhancedDocumentProcessingService encrypts clause content at rest"""
        from backend.application.services.enhanced_document_processing_service import EnhancedDocumentProcessingService
        
        mock_agent_manager = MagicMock()
        service = EnhancedDocumentProcessingService(mock_agent_manager)
        service.graph = MagicMock()

        mock_processing_result = MagicMock()
        mock_processing_result.document_embeddings = []
        mock_processing_result.relationship_embeddings = []
        
        mock_clause_embedding = MagicMock()
        mock_clause_embedding.metadata = {"start_position": 10, "end_position": 50, "clause_type": "Payment"}
        mock_clause_embedding.embedding = [0.1, 0.2, 0.3]
        mock_clause_embedding.content = "Confidential payment terms text"
        
        mock_processing_result.clause_embeddings = [mock_clause_embedding]

        service._store_enhanced_embeddings("contract-test-1", "tenant-xyz", mock_processing_result)

        # Inspect the query called on service.graph.query
        clause_queries = [
            call for call in service.graph.query.call_args_list
            if "CONTAINS_CLAUSE" in call.args[0]
        ]
        self.assertEqual(len(clause_queries), 1)

        cypher_str, query_params = clause_queries[0].args
        stored_content = query_params["content"]

        # Content must NOT be raw plaintext
        self.assertNotEqual(stored_content, "Confidential payment terms text")

        # Decrypting stored content using field_encryptor must yield the original text
        decrypted = field_encryptor.decrypt(stored_content)
        self.assertEqual(decrypted, "Confidential payment terms text")


if __name__ == "__main__":
    unittest.main()
