"""
Regression tests for production-readiness audit finding #3:
GET /debug/contracts defaulted to *enabled* whenever ENVIRONMENT was
unset (`os.getenv("ENVIRONMENT", "development") != "production"` -
fail-open), and had zero tenant_id filtering in its Cypher - any
authenticated caller with VIEW_AUDIT permission (any role, any tenant)
saw every tenant's contracts.

Two independent fixes, both covered here:
1. create_debug_router() now fails *closed* - ENVIRONMENT must be
   explicitly set to something other than "production" to enable these
   routes at all; unset means hidden, not shown.
2. Even when enabled, /debug/contracts and /debug/contract-types are now
   tenant-scoped via the validated JWT identity, same as every real route
   - a debug endpoint accidentally left on in a shared environment can no
   longer leak cross-tenant data.
"""

import os
import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api.routes.debug import create_debug_router, _debug_routes_enabled
    from backend.infrastructure.contract_repository import Neo4jContractRepository

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tests.conftest import auth_headers


class DebugRoutesEnabledFlagTests(unittest.TestCase):
    def test_unset_environment_is_disabled_fail_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENVIRONMENT", None)
            self.assertFalse(_debug_routes_enabled())

    def test_production_is_disabled(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            self.assertFalse(_debug_routes_enabled())

    def test_explicit_non_production_is_enabled(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
            self.assertTrue(_debug_routes_enabled())
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            self.assertTrue(_debug_routes_enabled())

    def test_router_is_empty_when_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENVIRONMENT", None)
            router = create_debug_router()
        self.assertEqual(router.routes, [])

    def test_router_has_routes_when_enabled(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
            router = create_debug_router()
        paths = {r.path for r in router.routes}
        self.assertIn("/debug/contracts", paths)
        self.assertIn("/debug/contract-types", paths)


class FakeTenantScopedGraph:
    """Mirrors MATCH (c:Contract {tenant_id: $tenant_id}) - only returns
    rows for the tenant actually asked for, same as a real Neo4j graph."""

    def __init__(self):
        self.contracts = []

    def add_contract(self, tenant_id, file_id, contract_type="MSA", summary="A contract", source="upload"):
        self.contracts.append({
            "tenant_id": tenant_id, "file_id": file_id,
            "contract_type": contract_type, "summary": summary, "source": source,
        })

    def query(self, cypher, params=None):
        params = params or {}
        tenant_id = params.get("tenant_id")
        matching = [c for c in self.contracts if c["tenant_id"] == tenant_id]

        if "c.contract_type as contract_type, count(*) as count" in cypher:
            counts = {}
            for c in matching:
                counts[c["contract_type"]] = counts.get(c["contract_type"], 0) + 1
            return [{"contract_type": t, "count": n} for t, n in counts.items()]

        return [
            {"contract_id": c["file_id"], "contract_type": c["contract_type"],
             "summary": c["summary"], "source": c["source"]}
            for c in matching
        ]


class DebugContractsCrossTenantIsolationTests(unittest.TestCase):
    """The concrete before/after proof: two tenants, real tenant-scoped
    fake graph, hit the actual route through a real FastAPI TestClient."""

    def setUp(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
            router = create_debug_router()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

        self.fake_graph = FakeTenantScopedGraph()
        self.fake_graph.add_contract("tenant_a", "CONTRACT_A1", summary="Tenant A's confidential MSA")
        self.fake_graph.add_contract("tenant_a", "CONTRACT_A2", summary="Tenant A's second contract")
        self.fake_graph.add_contract("tenant_b", "CONTRACT_B1", summary="Tenant B's confidential NDA")

    def test_tenant_a_sees_only_its_own_contracts(self):
        fake_graph = self.fake_graph
        with patch.object(Neo4jContractRepository, "__init__", lambda self: setattr(self, "graph", fake_graph)):
            response = self.client.get("/debug/contracts", headers=auth_headers(tenant_id="tenant_a", role="AUDITOR"))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tenant_id"], "tenant_a")
        self.assertEqual(body["total_contracts"], 2)
        contract_ids = {c["contract_id"] for c in body["contracts"]}
        self.assertEqual(contract_ids, {"CONTRACT_A1", "CONTRACT_A2"})
        # The actual regression proof: tenant B's contract must not appear.
        self.assertNotIn("CONTRACT_B1", contract_ids)

    def test_tenant_b_sees_only_its_own_contract_not_tenant_as(self):
        fake_graph = self.fake_graph
        with patch.object(Neo4jContractRepository, "__init__", lambda self: setattr(self, "graph", fake_graph)):
            response = self.client.get("/debug/contracts", headers=auth_headers(tenant_id="tenant_b", role="AUDITOR"))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tenant_id"], "tenant_b")
        self.assertEqual(body["total_contracts"], 1)
        self.assertEqual(body["contracts"][0]["contract_id"], "CONTRACT_B1")

    def test_debug_route_requires_a_valid_token(self):
        response = self.client.get("/debug/contracts")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
