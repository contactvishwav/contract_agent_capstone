"""
Regression test for the PolicyService/PolicyWorkflowOrchestrator
construction bug.

This originally guarded against a SupervisorAgent() argument-count bug:
three call sites constructed it with the wrong number of positional
arguments and raised TypeError immediately, one of which
(PolicyWorkflowOrchestrator, via PolicyService) sat directly in the live
POST /api/policies/upload route - every call to that endpoint would raise
TypeError before any policy logic ran.

The SupervisorAgent-specific tests were removed when the Supervisor
orchestration path was deleted as dead weight (it was never called by
either PolicyWorkflowOrchestrator or PolicyWorkflowSupervisor - both
always ran their own manual step loop instead; see
docs/CAPSTONE_SUMMARY.md's orchestration-consolidation decision). The one
test below still applies: constructing PolicyService() - and everything
it builds along the way - must not raise.
"""

import unittest
from unittest.mock import patch

# Mock Neo4j and Gemini BEFORE importing backend modules that instantiate them
# at module level (e.g. backend/shared/utils/contract_search_tool.py:53 builds
# a real Neo4jGraph and calls verify_connectivity() on import). Same pattern as
# backend/tests/test_mcp_capabilities.py.
with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.policy_workflow_orchestrator import PolicyWorkflowOrchestrator
    from backend.application.services.policy_service import PolicyService


class TestPolicyUploadSmokeTest(unittest.TestCase):
    """
    Smoke test for the live route this bug actually affects: POST
    /api/policies/upload constructs PolicyService() directly in the request
    handler (backend/api/policy_api.py:53), which in turn constructs
    PolicyWorkflowOrchestrator() (backend/application/services/policy_service.py:20).
    Before the fix, this construction alone raised TypeError, so every upload
    request returned HTTP 500 regardless of the uploaded content.

    This test instantiates the service directly rather than going through
    FastAPI's TestClient, since the route's own try/except would otherwise
    swallow the very TypeError we need to assert doesn't happen. Constructing
    PolicyService() is exactly the line that used to blow up.
    """

    def test_policy_service_construction_does_not_raise_typeerror(self):
        try:
            service = PolicyService()
        except TypeError as e:
            self.fail(f"PolicyService() construction raised TypeError: {e}")

        self.assertIsInstance(service.orchestrator, PolicyWorkflowOrchestrator)


if __name__ == "__main__":
    unittest.main()
