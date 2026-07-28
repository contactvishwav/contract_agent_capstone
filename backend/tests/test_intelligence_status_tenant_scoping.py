"""
Regression test: get_intelligence_status and get_intelligence_dashboard
(backend/api/contract_intelligence.py) previously bound the literal string
"default-tenant" into their Cypher query params regardless of who called
them, ignoring the actual caller entirely - two tenants' status/dashboard
requests could never be distinguished, and neither could read its own real
data unless it happened to be named "default-tenant".

This test confirms both endpoints now accept and use the caller-supplied
tenant_id in the actual query sent to Neo4j, and that two different tenants
querying the same contract_id get separately-scoped queries (not a shared
"default-tenant" bucket).
"""

import asyncio
import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api import contract_intelligence


class TestIntelligenceStatusTenantScoping(unittest.TestCase):
    def setUp(self):
        self.fake_graph = MagicMock()
        self.fake_graph.query.return_value = [{
            "status": "completed", "risk_score": 42.0, "risk_level": "MEDIUM",
            "violations_count": 1, "clauses_count": 5, "redlines_count": 1,
            "processing_time": 1.2, "updated": "2026-01-01T00:00:00Z",
        }]
        self._patcher = patch.object(contract_intelligence.repository, "graph", self.fake_graph)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_get_intelligence_status_uses_caller_tenant_id(self):
        asyncio.run(contract_intelligence.get_intelligence_status(
            contract_id="CNT1", tenant_id="tenant_b"
        ))
        _, params = self.fake_graph.query.call_args[0]
        self.assertEqual(params["tenant_id"], "tenant_b")
        self.assertNotEqual(params["tenant_id"], "default-tenant")

    def test_get_intelligence_status_scopes_differently_per_tenant(self):
        asyncio.run(contract_intelligence.get_intelligence_status(contract_id="CNT1", tenant_id="tenant_a"))
        _, params_a = self.fake_graph.query.call_args[0]

        asyncio.run(contract_intelligence.get_intelligence_status(contract_id="CNT1", tenant_id="tenant_b"))
        _, params_b = self.fake_graph.query.call_args[0]

        self.assertNotEqual(params_a["tenant_id"], params_b["tenant_id"])


class TestIntelligenceDashboardTenantScoping(unittest.TestCase):
    def setUp(self):
        self.fake_graph = MagicMock()
        self.fake_graph.query.return_value = [{
            "total_analyzed": 3, "avg_risk_score": 55.0, "high_risk_count": 1,
            "total_violations": 2, "total_clauses": 10, "total_redlines": 2,
        }]
        self._patcher = patch.object(contract_intelligence.repository, "graph", self.fake_graph)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_get_intelligence_dashboard_uses_caller_tenant_id(self):
        asyncio.run(contract_intelligence.get_intelligence_dashboard(tenant_id="tenant_b"))
        _, params = self.fake_graph.query.call_args[0]
        self.assertEqual(params["tenant_id"], "tenant_b")
        self.assertNotEqual(params["tenant_id"], "default-tenant")

    def test_get_intelligence_dashboard_scopes_differently_per_tenant(self):
        asyncio.run(contract_intelligence.get_intelligence_dashboard(tenant_id="tenant_a"))
        _, params_a = self.fake_graph.query.call_args[0]

        asyncio.run(contract_intelligence.get_intelligence_dashboard(tenant_id="tenant_b"))
        _, params_b = self.fake_graph.query.call_args[0]

        self.assertNotEqual(params_a["tenant_id"], params_b["tenant_id"])


if __name__ == "__main__":
    unittest.main()
