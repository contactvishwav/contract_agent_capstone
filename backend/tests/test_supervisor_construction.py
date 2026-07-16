"""
Regression tests for the SupervisorAgent() argument-count bug.

SupervisorAgent.__init__ requires (registry, quality_manager) as positional
arguments (backend/agents/supervisor/supervisor_agent.py:39). Three call sites
previously constructed it with the wrong number of arguments and raised
TypeError immediately:
  - backend/agents/supervisor/factory.py:14           -> SupervisorAgent(registry)          (missing quality_manager)
  - backend/agents/policy_workflow_orchestrator.py:14  -> SupervisorAgent()                  (zero args)
  - backend/agents/policy_workflow_supervisor.py:19    -> SupervisorAgent()                  (zero args)

One of these (PolicyWorkflowOrchestrator, via PolicyService) sits directly in
the live POST /api/policies/upload route, so every call to that endpoint would
raise TypeError before any policy logic ran.
"""

import unittest
from unittest.mock import patch

# Mock Neo4j and Gemini BEFORE importing backend modules that instantiate them
# at module level (e.g. backend/shared/utils/contract_search_tool.py:53 builds
# a real Neo4jGraph and calls verify_connectivity() on import). Same pattern as
# backend/tests/test_mcp_capabilities.py.
with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.supervisor.supervisor_agent import SupervisorAgent
    from backend.agents.supervisor.factory import SupervisorFactory as OrphanSupervisorFactory
    from backend.agents.policy_workflow_orchestrator import PolicyWorkflowOrchestrator
    from backend.agents.policy_workflow_supervisor import PolicyWorkflowSupervisor
    from backend.application.services.policy_service import PolicyService


class TestSupervisorConstruction(unittest.TestCase):
    """Each of these previously raised TypeError on construction; now they must not."""

    def test_supervisor_factory_create_supervisor(self):
        supervisor = OrphanSupervisorFactory.create_supervisor()
        self.assertIsInstance(supervisor, SupervisorAgent)
        self.assertIsNotNone(supervisor.quality_manager)

    def test_policy_workflow_orchestrator_construction(self):
        orchestrator = PolicyWorkflowOrchestrator()
        self.assertIsInstance(orchestrator.supervisor, SupervisorAgent)
        self.assertIsNotNone(orchestrator.supervisor.quality_manager)

    def test_policy_workflow_supervisor_construction(self):
        supervisor_wrapper = PolicyWorkflowSupervisor()
        self.assertIsInstance(supervisor_wrapper.supervisor, SupervisorAgent)
        self.assertIsNotNone(supervisor_wrapper.supervisor.quality_manager)


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
        self.assertIsInstance(service.orchestrator.supervisor, SupervisorAgent)


if __name__ == "__main__":
    unittest.main()
