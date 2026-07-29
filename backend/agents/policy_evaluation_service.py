"""
Centralized LLM-based per-clause policy-violation reasoning.

Mirrors LLMExtractionService's discipline (backend/agents/llm_extraction_
service.py): a single well-scoped structured-output call, temperature 0,
and independent verification of the model's output rather than trusting it
at face value. There, offsets are recomputed by searching the source text
rather than trusted from the LLM; here, a returned rule_id is checked
against the exact set of rule ids actually offered to the model - never
trusted as a citation of a real rule on its own. This is what prevents a
violation from citing a policy rule that doesn't exist.
"""

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from backend.domain.policies.entities import PolicyRule
from backend.shared.utils.llm_concurrency import llm_call_semaphore
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


class _LLMPolicyViolation(BaseModel):
    rule_id: str = Field(
        description="The id of the PolicyRule this clause violates - must be "
                    "copied exactly from the list provided, never invented"
    )
    issue: str = Field(description="Specific reason this clause violates the cited rule")
    severity: str = Field(description="CRITICAL, HIGH, MEDIUM, or LOW")
    suggested_fix: str = Field(description="Concrete redline suggestion to bring the clause into compliance")
    confidence: float = Field(ge=0.0, le=1.0)


class _LLMPolicyEvaluationResponse(BaseModel):
    violations: List[_LLMPolicyViolation] = Field(default_factory=list)


class PolicyEvaluationService:
    """
    Owns the prompt, the structured-output call, and grounding verification
    for LLM-based per-clause policy evaluation. One call per clause (not a
    single call batching every clause in the contract) so each evaluation
    stays scoped to exactly the rules applicable to that one clause.
    """

    def __init__(self, llm=None):
        self.llm = llm
        self._structured_llm = llm.with_structured_output(_LLMPolicyEvaluationResponse) if llm else None

    def evaluate_clause(
        self, clause_type: str, clause_text: str, rules: List[PolicyRule]
    ) -> List[Dict[str, Any]]:
        """
        Evaluate a single clause against its applicable policy rules.

        Returns [] immediately, without making any LLM call, if there are
        no rules to check against - there is nothing to evaluate, so
        nothing should be fabricated. This is the mechanical anti-
        hallucination guarantee for the "no applicable policy" case.
        """
        if not rules or not self._structured_llm or not clause_text or not clause_text.strip():
            return []

        valid_rule_ids = {r.id for r in rules}
        prompt = self._build_prompt(clause_type, clause_text, rules)

        try:
            with llm_call_semaphore:
                response = self._structured_llm.invoke(prompt)
        except Exception as e:
            logger.error(f"Policy evaluation failed: {e}")
            return []

        violations = []
        for v in response.violations:
            if v.rule_id not in valid_rule_ids:
                # Grounding check, mirroring LLMExtractionService's offset
                # verification: don't trust the LLM's citation at face
                # value. A rule_id that wasn't actually offered means the
                # model hallucinated a policy that doesn't exist - discard.
                logger.warning(f"Discarding policy violation citing unknown rule_id: {v.rule_id}")
                continue
            violations.append({
                "rule_id": v.rule_id,
                "issue": v.issue,
                "severity": v.severity,
                "suggested_fix": v.suggested_fix,
                "confidence": v.confidence,
            })
        return violations

    def _build_prompt(self, clause_type: str, clause_text: str, rules: List[PolicyRule]) -> str:
        rule_list = "\n".join(
            f"- rule_id: {r.id} | severity if violated: {r.severity} | {r.rule_text}"
            for r in rules
        )
        return f"""You are a contract policy-compliance reviewer. Given a single
extracted contract clause and a list of company policy rules, determine
whether the clause violates any of the rules.

Clause type: {clause_type}
Clause text:
{clause_text}

Policy rules (only cite a rule_id copied exactly from this list - never invent one):
{rule_list}

For each rule this specific clause actually violates, report the rule_id
exactly as given above, the specific issue, severity, a concrete suggested
redline, and your confidence (0.0-1.0). If the clause does not violate any
rule, return no violations. Do not report a violation for a rule that
doesn't apply to this clause."""
