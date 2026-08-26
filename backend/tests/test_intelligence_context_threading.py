"""
Regression test: contract_id/tenant_id were dropped one layer below
ContractIntelligenceService.analyze_contract_by_id (which has both) - neither
ever reached IntelligenceOrchestrator.analyze_contract or _analyze_traditional's
initial_state. Code elsewhere already assumed this worked (_pattern_analysis
did state.get('contract_id', 'unknown')), but it always resolved to
'unknown' since nothing ever put a real value into state.

This matters because audit logging (P1 item 2) and clause-id generation
(P1 item 3) both need a real contract_id/tenant_id available inside the
orchestration state to be meaningful.
"""

import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator


class TestTraditionalPathReceivesContext(unittest.TestCase):
    def test_initial_state_carries_contract_and_tenant_id(self):
        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None  # bypassing __init__, which normally sets this
        captured = {}

        class FakeWorkflow:
            def invoke(self, state, config=None):
                captured.update(state)
                return {
                    "extracted_clauses": [], "policy_violations": [], "risk_data": {},
                    "redline_suggestions": [], "is_complete": True, "node_status": {},
                }

        orchestrator.workflow = FakeWorkflow()

        orchestrator._analyze_traditional("some contract text", contract_id="c1", tenant_id="t1")

        self.assertEqual(captured.get("contract_id"), "c1")
        self.assertEqual(captured.get("tenant_id"), "t1")
        self.assertNotEqual(captured.get("contract_id"), "unknown")


if __name__ == "__main__":
    unittest.main()
