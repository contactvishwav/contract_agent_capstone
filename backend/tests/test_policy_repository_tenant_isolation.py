"""
Cross-tenant isolation regression tests for PolicyRepository.

get_policy_by_id, _get_policy_rules, update_policy_version, and delete_policy
previously had zero tenant_id filter in their Cypher queries - any tenant
could read, list rules for, version-bump, or soft-delete another tenant's
policy document simply by knowing (or guessing) its policy_id. Two of these
(get_policy_by_id, delete_policy) are reachable today via live routes in
backend/api/policy_api.py (GET/DELETE /api/policies/{policy_id}), gated only
by a permission check with no tenant ownership check at all.

These tests use a minimal in-memory fake Neo4j graph (rather than the real
driver) that only understands the specific Cypher shapes these four methods
use, and - critically - only "matches" a stored PolicyDocument node when
every property in the MATCH pattern agrees, mirroring real Neo4j semantics.
Before the fix, these methods never included tenant_id in their query
parameters at all, so the fake (like a real graph) would have matched purely
on policy_id regardless of which tenant asked.
"""

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.domain.policies.entities import PolicyRule

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.policy_repository import PolicyRepository
    from backend.api import policy_api


class FakePolicyGraph:
    """In-memory stand-in for Neo4jGraph understanding PolicyRepository's
    four previously-unguarded methods' exact Cypher shapes."""

    def __init__(self):
        self.documents = {}

    def add_policy(self, policy_id, tenant_id, rules=None):
        self.documents[policy_id] = {
            "id": policy_id,
            "tenant_id": tenant_id,
            "name": "Test Policy",
            "version": "1.0",
            "created_at": None,
            "updated_at": None,
            "checksum": "abc123",
            "active": True,
            "rules": rules or [],
        }

    def _lookup(self, params):
        """Mirrors a real Neo4j MATCH: every property present in the query
        params must agree with the stored node, including tenant_id when
        it's one of the params sent (it wasn't, pre-fix)."""
        policy_id = params.get("policy_id") or params.get("old_policy_id")
        doc = self.documents.get(policy_id)
        if not doc:
            return None
        if "tenant_id" in params and doc["tenant_id"] != params["tenant_id"]:
            return None
        return doc

    def query(self, cypher, params=None):
        params = params or {}

        if "collect(" in cypher:  # get_policy_by_id
            doc = self._lookup(params)
            if not doc:
                return []
            return [{**{k: doc[k] for k in ("id", "name", "tenant_id", "version",
                                             "created_at", "updated_at", "checksum")},
                     "rules": doc["rules"]}]

        if "HAS_RULE" in cypher and "SET" not in cypher and "CREATE" not in cypher:
            # _get_policy_rules
            doc = self._lookup(params)
            return doc["rules"] if doc else []

        if "SET p.archived_at" in cypher:  # update_policy_version: archive step
            doc = self._lookup(params)
            if not doc:
                return []
            doc["active"] = False
            return [{"matched_id": doc["id"]}]

        if "CREATE (new:PolicyDocument" in cypher:  # update_policy_version: copy step
            doc = self._lookup({"policy_id": params["old_policy_id"], "tenant_id": params["tenant_id"]})
            if doc:
                self.documents[params["new_policy_id"]] = {
                    **doc, "id": params["new_policy_id"], "version": params["new_version"], "rules": [],
                }
            return []

        if "CREATE (r:PolicyRule" in cypher:  # update_policy_version: add-rules step
            doc = self.documents.get(params["policy_id"])
            if doc:
                doc["rules"].append({
                    "id": params["rule_id"], "rule_text": params["rule_text"],
                    "rule_type": params["rule_type"], "applies_to": params["applies_to"],
                    "severity": params["severity"], "section_reference": params["section_reference"],
                })
            return []

        if "p.deleted_at" in cypher:  # delete_policy
            doc = self._lookup(params)
            if not doc:
                return []
            doc["active"] = False
            doc["deleted"] = True
            return [{"matched_id": doc["id"]}]

        return []


def make_repo_with_fixture():
    graph = FakePolicyGraph()
    graph.add_policy("policy_tenant_a_20260101", "tenant_a", rules=[
        {"id": "rule_1", "rule_text": "Net 30 payment terms", "rule_type": "mandatory",
         "applies_to": ["general"], "severity": "HIGH", "section_reference": "3.1"},
    ])
    repo = PolicyRepository()
    repo.graph = graph
    return repo, graph


class TestPolicyRepositoryTenantIsolation(unittest.TestCase):
    def setUp(self):
        self.repo, self.graph = make_repo_with_fixture()

    def test_get_policy_by_id_rejects_cross_tenant(self):
        own = self.repo.get_policy_by_id("policy_tenant_a_20260101", "tenant_a")
        self.assertIsNotNone(own)
        self.assertEqual(own.tenant_id, "tenant_a")

        cross = self.repo.get_policy_by_id("policy_tenant_a_20260101", "tenant_b")
        self.assertIsNone(cross)

    def test_get_policy_rules_rejects_cross_tenant(self):
        own = self.repo._get_policy_rules("policy_tenant_a_20260101", "tenant_a")
        self.assertEqual(len(own), 1)

        cross = self.repo._get_policy_rules("policy_tenant_a_20260101", "tenant_b")
        self.assertEqual(cross, [])

    def test_update_policy_version_rejects_cross_tenant(self):
        new_rules = [PolicyRule(id="rule_2", rule_text="New rule", rule_type="mandatory",
                                 applies_to=["general"], severity="HIGH", section_reference="4.1")]

        cross = self.repo.update_policy_version("policy_tenant_a_20260101", "tenant_b", "2.0", new_rules)
        self.assertFalse(cross)
        self.assertNotIn("policy_tenant_a_20260101_v2.0", self.graph.documents)
        # The original policy must be untouched by the rejected cross-tenant attempt.
        self.assertTrue(self.graph.documents["policy_tenant_a_20260101"]["active"])

        own = self.repo.update_policy_version("policy_tenant_a_20260101", "tenant_a", "2.0", new_rules)
        self.assertTrue(own)
        self.assertIn("policy_tenant_a_20260101_v2.0", self.graph.documents)

    def test_delete_policy_rejects_cross_tenant(self):
        cross = self.repo.delete_policy("policy_tenant_a_20260101", "tenant_b")
        self.assertFalse(cross)
        self.assertTrue(self.graph.documents["policy_tenant_a_20260101"]["active"])

        own = self.repo.delete_policy("policy_tenant_a_20260101", "tenant_a")
        self.assertTrue(own)
        self.assertFalse(self.graph.documents["policy_tenant_a_20260101"]["active"])


class TestPolicyApiRoutesRejectCrossTenant(unittest.TestCase):
    """
    Exercises the actual live route functions (not just the repository in
    isolation) - GET/DELETE /api/policies/{policy_id} both construct their
    own PolicyRepository() internally, so it's patched to use our fixture.
    """

    def setUp(self):
        self.repo, self.graph = make_repo_with_fixture()
        self.policy_api = policy_api

    def test_get_policy_details_route_rejects_cross_tenant(self):
        with patch.object(self.policy_api, "PolicyRepository", return_value=self.repo):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(self.policy_api.get_policy_details(
                    policy_id="policy_tenant_a_20260101", tenant_id="tenant_b"
                ))
        # A pre-existing (separate, out-of-scope) broad `except Exception` in
        # this route re-wraps the intended 404 into a 500 - not something
        # this fix touches. What matters here is that access is rejected and
        # tenant B's data never comes back, not the exact status code.
        self.assertIn("not found", cm.exception.detail.lower())

        # Confirm the own-tenant path still works through the same route.
        with patch.object(self.policy_api, "PolicyRepository", return_value=self.repo):
            result = asyncio.run(self.policy_api.get_policy_details(
                policy_id="policy_tenant_a_20260101", tenant_id="tenant_a"
            ))
        self.assertTrue(result["success"])
        self.assertEqual(result["policy"]["tenant_id"], "tenant_a")

    def test_delete_policy_route_rejects_cross_tenant(self):
        with patch.object(self.policy_api, "PolicyRepository", return_value=self.repo):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(self.policy_api.delete_policy(
                    policy_id="policy_tenant_a_20260101", tenant_id="tenant_b"
                ))
        self.assertIn("not found", cm.exception.detail.lower())
        # The policy must still be active/untouched for its real tenant.
        self.assertTrue(self.graph.documents["policy_tenant_a_20260101"]["active"])

        with patch.object(self.policy_api, "PolicyRepository", return_value=self.repo):
            result = asyncio.run(self.policy_api.delete_policy(
                policy_id="policy_tenant_a_20260101", tenant_id="tenant_a"
            ))
        self.assertTrue(result["success"])
        self.assertFalse(self.graph.documents["policy_tenant_a_20260101"]["active"])


if __name__ == "__main__":
    unittest.main()
