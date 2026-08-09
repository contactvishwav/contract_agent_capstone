"""
Regression tests for reliability/observability audit finding #7:
GET /api/documents/enhanced/embedding-status/{contract_id} and
EnhancedDocumentProcessingService.__init__ each constructed a brand new
Neo4jGraph (and therefore a brand new underlying Bolt driver/connection
pool) on every single call - a request-scoped or per-upload leak, never
closed, unbounded over the life of the process. Both now reuse the same
module-level singleton every other call site in this codebase already
shares (backend/shared/utils/contract_search_tool.py:54-56, `graph`).

These tests prove reuse concretely - not just "no error thrown" - by
asserting object identity across repeated calls/instances, and by
counting how many times Neo4jGraph() itself is actually constructed.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.shared.utils import contract_search_tool
    from backend.api.enhanced_document_upload import get_embedding_status
    from backend.governance.auth import TokenIdentity
    from backend.application.services.enhanced_document_processing_service import (
        EnhancedDocumentProcessingService,
    )


class EmbeddingStatusRouteReusesSingletonGraphTests(unittest.TestCase):
    """GET /embedding-status/{contract_id} previously did
    `graph = Neo4jGraph(...)` inline on every request."""

    def test_repeated_calls_use_the_same_graph_object(self):
        identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN", username="tester")
        fake_graph = MagicMock()
        fake_graph.query.return_value = [{
            "has_document_embedding": True,
            "has_summary_embedding": True,
            "section_count": 2,
            "clause_count": 5,
            "relationship_count": 1,
            "relationship_embeddings": 1,
        }]

        with patch.object(contract_search_tool, "graph", fake_graph):
            asyncio.run(get_embedding_status("contract-1", identity))
            asyncio.run(get_embedding_status("contract-2", identity))
            asyncio.run(get_embedding_status("contract-3", identity))

        # Every call issued its query against the exact same driver
        # instance - none constructed a new one.
        self.assertEqual(fake_graph.query.call_count, 3)

    def test_route_never_constructs_a_new_neo4jgraph(self):
        identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN", username="tester")
        with patch("langchain_neo4j.Neo4jGraph") as ctor:
            with patch.object(contract_search_tool, "graph", MagicMock(
                query=MagicMock(return_value=[{
                    "has_document_embedding": False, "has_summary_embedding": False,
                    "section_count": 0, "clause_count": 0,
                    "relationship_count": 0, "relationship_embeddings": 0,
                }])
            )):
                asyncio.run(get_embedding_status("contract-1", identity))
            ctor.assert_not_called()


class EnhancedDocumentProcessingServiceReusesSingletonGraphTests(unittest.TestCase):
    """__init__ previously did self.graph = Neo4jGraph(...) - a new driver
    for every uploaded document, since the service is constructed fresh
    per upload (EnhancedDocumentServiceFactory.create_service)."""

    def test_multiple_service_instances_share_one_graph_object(self):
        agent_manager = MagicMock()

        with patch("backend.application.services.enhanced_document_processing_service.EmbeddingOrchestrator"), \
             patch("backend.application.services.enhanced_document_processing_service.EmbeddingValidator"), \
             patch("backend.application.services.enhanced_document_processing_service.PDFAgentFactory"):
            service_a = EnhancedDocumentProcessingService(agent_manager)
            service_b = EnhancedDocumentProcessingService(agent_manager)
            service_c = EnhancedDocumentProcessingService(agent_manager)

        # All three "per-upload" instances point at the exact same shared
        # graph object - constructing the service N times creates zero
        # new Neo4j connections, not N of them.
        self.assertIs(service_a.graph, contract_search_tool.graph)
        self.assertIs(service_b.graph, contract_search_tool.graph)
        self.assertIs(service_c.graph, contract_search_tool.graph)

    def test_service_construction_never_constructs_a_new_neo4jgraph(self):
        agent_manager = MagicMock()
        with patch("backend.application.services.enhanced_document_processing_service.EmbeddingOrchestrator"), \
             patch("backend.application.services.enhanced_document_processing_service.EmbeddingValidator"), \
             patch("backend.application.services.enhanced_document_processing_service.PDFAgentFactory"), \
             patch("langchain_neo4j.Neo4jGraph") as ctor:
            EnhancedDocumentProcessingService(agent_manager)
            EnhancedDocumentProcessingService(agent_manager)

        ctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
