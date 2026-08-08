"""
Regression test for a live end-to-end testing finding: a real LLM failure
(quota exhaustion, network error, parsing failure) during policy checking
used to be swallowed silently, per clause, all the way down in
PolicyEvaluationService.evaluate_clause (backend/agents/policy_evaluation_
service.py), which caught any exception and returned [] -
indistinguishable from "the model evaluated this clause and found nothing."
PolicyCheckerTool._run looped over this with no visibility into whether an
individual clause's evaluation actually succeeded, so a real failure
produced a false "success" audit entry and a clean-looking (but wrong)
"0 violations" result - which then propagated through _check_policies/
_execute_policy_check as node_status "success"/"failed" (never anything in
between), letting a genuinely dishonest "processing_complete: True" /
"intelligence_status: completed" reach the API and the persisted Neo4j
status.

Confirmed via a real live run against the actual Gemini API (not a mock):
clause extraction succeeded, then every policy-evaluation call hit a real
429 RESOURCE_EXHAUSTED, and the analysis still reported
"intelligence_status": "completed", "risk_level": "LOW", "violations_count":
0 with a "success" audit entry.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.intelligence_tools import PolicyCheckerTool
    from backend.agents.policy_evaluation_service import PolicyEvaluationService, _LLMPolicyEvaluationResponse, _LLMPolicyViolation
    from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator
    from backend.domain.policies.entities import PolicyRule

_audit_logger_patcher = patch("backend.agents.intelligence_tools.AuditLogger", return_value=MagicMock())
_audit_logger_patcher.start()

# Policy-evaluation reasoning tests, not caching - disable the content-hash
# cache so identical clause_type/text/rules across tests can't return a
# stale cached result.
_cache_disabled_patcher = patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False)
_cache_disabled_patcher.start()

_RULE = PolicyRule(
    id="rule_1", rule_text="Non-compete terms must be reasonable in scope.",
    rule_type="mandatory", applies_to=["general"], severity="HIGH", section_reference="s1",
)


class FlakyPolicyLLM:
    """Raises for any prompt containing `fail_on_text` (i.e. targeting one
    specific clause's content), succeeds with a real-looking violation for
    everything else - simulating a real quota/network failure that affects
    some but not all per-clause calls within a single PolicyCheckerTool
    run."""

    def __init__(self, fail_on_text, rule_id="rule_1"):
        self._fail_on = fail_on_text
        self._rule_id = rule_id

    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, prompt):
        if self._fail_on in prompt:
            raise RuntimeError("429 RESOURCE_EXHAUSTED (simulated quota failure)")
        parsed = _LLMPolicyEvaluationResponse(violations=[
            _LLMPolicyViolation(
                rule_id=self._rule_id, issue="Real issue", severity="HIGH",
                suggested_fix="Real fix", confidence=0.9,
            )
        ])
        return {
            "raw": SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}),
            "parsed": parsed,
            "parsing_error": None,
        }


class AlwaysFailsPolicyLLM:
    def with_structured_output(self, schema, include_raw=True):
        return self

    def invoke(self, prompt):
        raise RuntimeError("429 RESOURCE_EXHAUSTED (simulated quota failure)")


class PolicyEvaluationServiceRaisesOnFailureTests(unittest.TestCase):
    """The root fix: evaluate_clause must no longer swallow a real failure
    into an indistinguishable []."""

    def test_llm_invoke_failure_raises_instead_of_returning_empty(self):
        service = PolicyEvaluationService(AlwaysFailsPolicyLLM())
        with self.assertRaises(Exception):
            service.evaluate_clause("Non-Compete", "Employee shall not compete.", [_RULE])

    def test_no_parsed_result_raises_instead_of_returning_empty(self):
        class NoParsedLLM:
            def with_structured_output(self, schema, include_raw=True):
                return self

            def invoke(self, prompt):
                return {"raw": SimpleNamespace(usage_metadata=None), "parsed": None, "parsing_error": "bad output"}

        service = PolicyEvaluationService(NoParsedLLM())
        with self.assertRaises(Exception):
            service.evaluate_clause("Non-Compete", "Employee shall not compete.", [_RULE])


class PolicyCheckerToolPartialFailureTests(unittest.TestCase):
    """One clause fails to evaluate, one succeeds: the tool must report
    'partial', not silently mask the failed clause as a clean zero-
    violation result."""

    def _clauses(self):
        return [
            {"clause_id": "c1_non_compete_0", "clause_type": "Non-Compete", "content": "GOOD_CLAUSE: reasonable non-compete."},
            {"clause_id": "c1_non_compete_1", "clause_type": "Non-Compete", "content": "BAD_CLAUSE: this one blows up."},
        ]

    def test_one_failed_clause_reports_partial_status(self):
        with patch("backend.agents.intelligence_tools.get_applicable_rules", return_value=[_RULE]):
            tool = PolicyCheckerTool(llm=FlakyPolicyLLM(fail_on_text="BAD_CLAUSE"))
            result = json.loads(tool._run(json.dumps(self._clauses())))

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_clause_ids"], ["c1_non_compete_1"])
        # The clause that DID evaluate successfully must still show up -
        # the fix must not throw away good data just because a sibling
        # clause failed.
        self.assertEqual(len(result["violations"]), 1)
        self.assertEqual(result["violations"][0]["clause_id"], "c1_non_compete_0")

    def test_all_clauses_failing_reports_failure_status(self):
        with patch("backend.agents.intelligence_tools.get_applicable_rules", return_value=[_RULE]):
            tool = PolicyCheckerTool(llm=AlwaysFailsPolicyLLM())
            result = json.loads(tool._run(json.dumps(self._clauses())))

        self.assertEqual(result["status"], "failure")
        self.assertEqual(len(result["failed_clause_ids"]), 2)
        self.assertEqual(result["violations"], [])

    def test_no_failures_reports_success_status(self):
        with patch("backend.agents.intelligence_tools.get_applicable_rules", return_value=[_RULE]):
            tool = PolicyCheckerTool(llm=FlakyPolicyLLM(fail_on_text="NOTHING_MATCHES_THIS"))
            result = json.loads(tool._run(json.dumps(self._clauses())))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["failed_clause_ids"], [])
        self.assertEqual(len(result["violations"]), 2)

    def test_audit_log_reflects_partial_status_not_success(self):
        mock_audit_logger = MagicMock()
        with patch("backend.agents.intelligence_tools.AuditLogger", return_value=mock_audit_logger), \
             patch("backend.agents.intelligence_tools.get_applicable_rules", return_value=[_RULE]):
            tool = PolicyCheckerTool(llm=FlakyPolicyLLM(fail_on_text="BAD_CLAUSE"))
            tool._run(json.dumps(self._clauses()), contract_id="c1")

        _, kwargs = mock_audit_logger.log_event.call_args
        self.assertNotEqual(kwargs["status"], "success")
        self.assertEqual(kwargs["metadata"]["failed_clause_count"], 1)


class CheckPoliciesNodeStatusTests(unittest.TestCase):
    """_check_policies (the LangGraph node wrapping PolicyCheckerTool) must
    map the tool's honest status onto node_status, not flatten everything
    to 'success'."""

    def _state(self, extracted_clauses):
        return {
            "extracted_clauses": extracted_clauses,
            "contract_id": "c1", "tenant_id": "t1", "contract_type": "general",
            "node_status": {},
        }

    def test_partial_tool_status_becomes_partial_node_status(self):
        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None  # bypassing __init__, which normally sets this
        clauses = [
            {"clause_id": "c1_non_compete_0", "clause_type": "Non-Compete", "content": "GOOD_CLAUSE: fine."},
            {"clause_id": "c1_non_compete_1", "clause_type": "Non-Compete", "content": "BAD_CLAUSE: fails."},
        ]

        with patch("backend.agents.contract_intelligence_agents.PolicyCheckerTool") as MockTool:
            MockTool.return_value._run.return_value = json.dumps({
                "violations": [{"clause_id": "c1_non_compete_0", "severity": "HIGH"}],
                "failed_clause_ids": ["c1_non_compete_1"],
                "status": "partial",
            })
            result_state = orchestrator._check_policies(self._state(clauses))

        self.assertEqual(result_state["node_status"]["policy_checking"], "partial")
        self.assertEqual(len(result_state["policy_violations"]), 1)

    def test_failure_tool_status_becomes_error_node_status(self):
        orchestrator = IntelligenceOrchestrator.__new__(IntelligenceOrchestrator)
        orchestrator.llm = None  # bypassing __init__, which normally sets this
        clauses = [{"clause_id": "c1_non_compete_0", "clause_type": "Non-Compete", "content": "BAD_CLAUSE."}]

        with patch("backend.agents.contract_intelligence_agents.PolicyCheckerTool") as MockTool:
            MockTool.return_value._run.return_value = json.dumps({
                "violations": [], "failed_clause_ids": ["c1_non_compete_0"], "status": "failure",
            })
            result_state = orchestrator._check_policies(self._state(clauses))

        self.assertEqual(result_state["node_status"]["policy_checking"], "error")


if __name__ == "__main__":
    unittest.main()
