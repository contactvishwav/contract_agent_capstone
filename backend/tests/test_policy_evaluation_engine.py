"""
Tests for the P2 item 2 policy/risk engine upgrade: PolicyCheckerTool no
longer does keyword-matching against a hardcoded 6-category dict. It now
resolves the applicable PolicyRule set (a tenant's own uploaded policy via
PolicyRepository, or a small labeled default set if none exists), and
evaluates each clause against that set via either the deterministic table
(objective numeric thresholds - Cap On Liability, Minimum Commitment,
Notice Period To Terminate Renewal, Warranty Duration, Renewal Term) or
PolicyEvaluationService's LLM-based reasoning for everything else.

Every violation must cite the specific PolicyRule.id it was evaluated
against - never a free-text rule description - so these tests check the
rule_id on every violation produced.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.intelligence_tools import PolicyCheckerTool
    from backend.agents.policy_evaluation_service import PolicyEvaluationService
    from backend.domain.policies.entities import PolicyRule, PolicyDocument
    # Pre-import under the same Neo4j mock so patching PolicyRepository
    # below doesn't trigger a fresh, unmocked import of this module (which
    # constructs a real Neo4jGraph at module scope).
    import backend.infrastructure.policy_repository  # noqa: F401

_audit_logger_patcher = patch("backend.agents.intelligence_tools.AuditLogger", return_value=MagicMock())
_audit_logger_patcher.start()


class FakePolicyLLM:
    """Fake LLM that always cites a specific rule_id, regardless of clause content."""

    def __init__(self, rule_id_to_cite, severity="HIGH"):
        self._rule_id = rule_id_to_cite
        self._severity = severity

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        from backend.agents.policy_evaluation_service import _LLMPolicyEvaluationResponse, _LLMPolicyViolation
        return _LLMPolicyEvaluationResponse(violations=[
            _LLMPolicyViolation(
                rule_id=self._rule_id, issue="Test-fabricated issue", severity=self._severity,
                suggested_fix="Test-fabricated fix", confidence=0.9,
            )
        ])


class NeverCalledLLM:
    """Fails the test immediately if the LLM is ever invoked."""

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        raise AssertionError("LLM should never have been called - no applicable rules existed")


class TenantUploadedPolicyCitationTests(unittest.TestCase):
    def test_violation_cites_real_tenant_policy_rule_id(self):
        tenant_rule = PolicyRule(
            id="tenant_acme_rule_7", rule_text="Exclusivity commitments require Legal sign-off.",
            rule_type="mandatory", applies_to=["general"], severity="HIGH", section_reference="Section 4.2",
        )
        with patch("backend.infrastructure.policy_repository.PolicyRepository") as MockRepoClass:
            mock_repo = MockRepoClass.return_value
            mock_repo.get_policies_by_tenant.return_value = [
                PolicyDocument(id="policy_1", name="Acme Vendor Policy", tenant_id="tenant_acme",
                               version="1.0", rules=[tenant_rule], created_at=None, checksum="abc")
            ]
            mock_repo.get_applicable_policies.return_value = [tenant_rule]

            clauses = [{"clause_id": "c1_exclusivity_0", "clause_type": "Exclusivity", "content": "Vendor is the exclusive supplier."}]
            tool = PolicyCheckerTool(llm=FakePolicyLLM("tenant_acme_rule_7"))
            violations = json.loads(tool._run(json.dumps(clauses), tenant_id="tenant_acme", contract_type="general"))

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["rule_id"], "tenant_acme_rule_7")
        self.assertNotIn("default_", violations[0]["rule_id"])


class DefaultPolicySetFallbackTests(unittest.TestCase):
    def test_violation_cites_default_rule_when_tenant_has_no_policy(self):
        with patch("backend.infrastructure.policy_repository.PolicyRepository") as MockRepoClass:
            mock_repo = MockRepoClass.return_value
            mock_repo.get_policies_by_tenant.return_value = []  # tenant uploaded nothing

            clauses = [{"clause_id": "c1_exclusivity_0", "clause_type": "Exclusivity", "content": "Vendor is the exclusive supplier, indefinitely."}]
            tool = PolicyCheckerTool(llm=FakePolicyLLM("default_exclusivity_scope"))
            violations = json.loads(tool._run(json.dumps(clauses), tenant_id="tenant_new", contract_type="general"))

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["rule_id"], "default_exclusivity_scope")
        self.assertTrue(violations[0]["rule_id"].startswith("default_"))


class DeterministicTableNoLLMTests(unittest.TestCase):
    def test_cap_on_liability_evaluated_without_any_llm_call(self):
        clauses = [{
            "clause_id": "c1_cap_on_liability_0", "clause_type": "Cap On Liability",
            "content": "Liability shall not exceed 5 times the total fees paid.",
        }]
        # NeverCalledLLM would raise if PolicyEvaluationService ever invoked
        # it - proves the deterministic table short-circuits the LLM path
        # entirely for this category.
        tool = PolicyCheckerTool(llm=NeverCalledLLM())
        violations = json.loads(tool._run(json.dumps(clauses)))

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["rule_id"], "deterministic_cap_on_liability")
        self.assertIn("5.0x", violations[0]["issue"])

    def test_compliant_deterministic_clause_produces_no_violation(self):
        clauses = [{
            "clause_id": "c1_cap_on_liability_0", "clause_type": "Cap On Liability",
            "content": "Liability shall not exceed 1 times the total fees paid.",
        }]
        tool = PolicyCheckerTool(llm=NeverCalledLLM())
        violations = json.loads(tool._run(json.dumps(clauses)))

        self.assertEqual(violations, [])


class AntiHallucinationTests(unittest.TestCase):
    def test_no_violation_fabricated_when_no_rule_applies(self):
        # A category not in the deterministic table, with zero applicable
        # policy rules (tenant has none, and simulate an empty default set
        # for this contract_type) - PolicyEvaluationService.evaluate_clause
        # must return [] WITHOUT ever calling the LLM, since there is
        # nothing to evaluate against.
        with patch("backend.agents.intelligence_tools.get_applicable_rules", return_value=[]):
            clauses = [{"clause_id": "c1_governing_law_0", "clause_type": "Governing Law", "content": "California law applies."}]
            tool = PolicyCheckerTool(llm=NeverCalledLLM())
            violations = json.loads(tool._run(json.dumps(clauses), tenant_id="tenant_x"))

        self.assertEqual(violations, [])

    def test_llm_citing_unoffered_rule_id_is_discarded(self):
        # Direct unit test of the grounding check in PolicyEvaluationService
        # itself: even if the LLM hallucinates a citation for a rule that
        # was never offered to it, the violation must be discarded, not
        # trusted at face value.
        real_rule = PolicyRule(
            id="real_rule_1", rule_text="Real rule text.", rule_type="mandatory",
            applies_to=["general"], severity="HIGH", section_reference="Section 1",
        )
        service = PolicyEvaluationService(FakePolicyLLM("hallucinated_rule_id_999"))
        violations = service.evaluate_clause("Non-Compete", "Some clause text.", [real_rule])

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
