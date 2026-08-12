"""
Unit tests for Enhanced Search MODERATE phase items 4, 5, 6, and 7:
Item 4: Fix embedding-status endpoint to check Contract.embedding.
Item 5: Tenant-scoped rate limiting on Enhanced Search endpoints.
Item 6: Forwarding contract_type, active, and date range filters in _search_all_levels.
Item 7: Fix Cypher match in _store_enhanced_embeddings for relationship embeddings.
"""

import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from backend.domain.search_entities import SearchLevel, SearchParams, SearchResult
from backend.application.services.enhanced_search_service import EnhancedSearchService
from backend.shared.middleware.rate_limit import reset_rate_limit_storage
from backend.governance.auth import create_access_token


class EnhancedSearchModerateFixesTests(unittest.TestCase):

    def setUp(self):
        reset_rate_limit_storage()

    def test_item_4_embedding_status_checks_contract_embedding(self):
        """Item 4: embedding-status query checks c.embedding IS NOT NULL"""
        from backend.api.enhanced_document_upload import get_embedding_status
        from backend.governance.auth import TokenIdentity

        mock_graph = MagicMock()
        mock_graph.query.return_value = [{
            "has_contract_embedding": True,
            "has_document_embedding": False,
            "has_summary_embedding": False,
            "section_count": 2,
            "clause_count": 5,
            "relationship_count": 1,
            "relationship_embeddings": 1
        }]

        identity = TokenIdentity(tenant_id="tenant-item4", username="admin", role="ADMIN")

        with patch("backend.shared.utils.contract_search_tool.graph", mock_graph):
            import asyncio
            result = asyncio.run(get_embedding_status("contract-123", identity=identity))

        self.assertEqual(result["contract_id"], "contract-123")
        self.assertTrue(result["embedding_status"]["contract_embedding"])
        self.assertTrue(result["embedding_status"]["document_embedding"])
        self.assertEqual(result["total_embeddings"], 1 + 2 + 5 + 1)

        query_str = mock_graph.query.call_args[0][0]
        self.assertIn("c.embedding IS NOT NULL as has_contract_embedding", query_str)

    def test_item_6_search_all_levels_forwards_all_filters(self):
        """Item 6: _search_all_levels must forward contract_type, active, and date range filters"""
        service = EnhancedSearchService()
        executed_params = []

        for level in [SearchLevel.DOCUMENT, SearchLevel.SECTION, SearchLevel.CLAUSE, SearchLevel.RELATIONSHIP]:
            mock_strategy = MagicMock()
            def make_execute(l):
                def _exec(p):
                    executed_params.append(p)
                    return SearchResult(total_count=1, items=[{f"{l.value}_id": "1"}], search_metadata={})
                return _exec
            mock_strategy.execute.side_effect = make_execute(level)
            service._strategies[level] = mock_strategy

        params = SearchParams(
            search_level=SearchLevel.ALL,
            tenant_id="tenant-filter-test",
            query="test query",
            contract_type="MSA",
            active=True,
            min_effective_date="2025-01-01",
            max_effective_date="2025-12-31",
            min_end_date="2026-01-01",
            max_end_date="2027-12-31"
        )

        service.search(params)

        self.assertEqual(len(executed_params), 4)
        for p in executed_params:
            self.assertEqual(p.tenant_id, "tenant-filter-test")
            self.assertEqual(p.query, "test query")
            self.assertEqual(p.contract_type, "MSA")
            self.assertTrue(p.active)
            self.assertEqual(p.min_effective_date, "2025-01-01")
            self.assertEqual(p.max_effective_date, "2025-12-31")
            self.assertEqual(p.min_end_date, "2026-01-01")
            self.assertEqual(p.max_end_date, "2027-12-31")

    def test_item_7_store_enhanced_embeddings_relationship_cypher_match(self):
        """Item 7: _store_enhanced_embeddings matches Party by name without p.tenant_id property"""
        from backend.application.services.enhanced_document_processing_service import EnhancedDocumentProcessingService

        mock_agent_manager = MagicMock()
        service = EnhancedDocumentProcessingService(mock_agent_manager)
        service.graph = MagicMock()

        mock_processing_result = MagicMock()
        mock_processing_result.document_embeddings = []
        mock_processing_result.clause_embeddings = []

        mock_rel = MagicMock()
        mock_rel.metadata = {"relationship_type": "PARTY_TO", "party_name": "Acme Corp"}
        mock_rel.embedding = [0.1, 0.2, 0.3]
        mock_rel.content = "Acme Corp is Client"

        mock_processing_result.relationship_embeddings = [mock_rel]

        service._store_enhanced_embeddings("contract-rel-1", "tenant-rel-1", mock_processing_result)

        rel_queries = [
            call for call in service.graph.query.call_args_list
            if "PARTY_TO" in call.args[0]
        ]
        self.assertEqual(len(rel_queries), 1)

        cypher_str, query_params = rel_queries[0].args
        self.assertIn("(p:Party {name: $party_name})", cypher_str)
        self.assertNotIn("p.tenant_id", cypher_str)
        self.assertEqual(query_params["party_name"], "Acme Corp")
        self.assertEqual(query_params["embedding"], [0.1, 0.2, 0.3])

    def test_item_5_rate_limiting_enhanced_search_endpoints(self):
        """Item 5: Tenant-scoped rate limiting applies to /api/contracts/search/enhanced"""
        from backend.main import app
        client = TestClient(app)

        token = create_access_token("tenant-rate-test", "ADMIN", username="admin_user")
        headers = {"Authorization": f"Bearer {token}"}

        with patch("backend.application.services.enhanced_search_service.EnhancedSearchService.search") as mock_search:
            mock_search.return_value = SearchResult(total_count=0, items=[], search_metadata={})

            # Fire request
            response = client.post(
                "/api/contracts/search/enhanced",
                headers=headers,
                json={"search_level": "document", "query": "test"}
            )
            self.assertEqual(response.status_code, 200)

            # Re-initialize rate limiter storage and verify key function
            from backend.shared.middleware.rate_limit import tenant_scoped_or_ip_key
            mock_req = MagicMock()
            mock_req.headers = {"Authorization": f"Bearer {token}"}
            key = tenant_scoped_or_ip_key(mock_req)
            self.assertEqual(key, "tenant:tenant-rate-test")


if __name__ == "__main__":
    unittest.main()
