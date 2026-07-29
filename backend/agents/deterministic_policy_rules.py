"""
Deterministic policy checks for the small set of CUAD categories where
compliance reduces to a single, objective, universal numeric comparison -
a multiplier, dollar amount, or duration - rather than a judgment call
about phrasing or business intent. Everything else is deliberately left to
PolicyEvaluationService's LLM-based reasoning instead of guessing here.

Adding a category to this table should require the same bar as the five
below: one canonical number, extractable via a tightly-scoped regex on the
dominant real-world phrasing, compared against a threshold that doesn't
meaningfully vary by tenant or contract type. If the number can't be
confidently extracted, this returns None rather than guessing - the clause
is simply left unchecked by the deterministic table (it may still be
covered by a tenant's own uploaded policy via the LLM path for other rules,
but this specific objective check just doesn't fire on ambiguous phrasing).
"""

import re
from typing import Any, Dict, Optional

from backend.domain.policies.entities import PolicyRule

DETERMINISTIC_RULES: Dict[str, PolicyRule] = {
    "Cap On Liability": PolicyRule(
        id="deterministic_cap_on_liability", rule_type="mandatory", applies_to=["general"],
        severity="HIGH", section_reference="Deterministic policy table",
        rule_text="Liability shall not exceed 2x the total fees paid or payable under the agreement.",
    ),
    "Minimum Commitment": PolicyRule(
        id="deterministic_minimum_commitment", rule_type="mandatory", applies_to=["general"],
        severity="MEDIUM", section_reference="Deterministic policy table",
        rule_text="Minimum purchase/spend commitments should not exceed $100,000 without Legal approval.",
    ),
    "Notice Period To Terminate Renewal": PolicyRule(
        id="deterministic_notice_period", rule_type="mandatory", applies_to=["general"],
        severity="MEDIUM", section_reference="Deterministic policy table",
        rule_text="At least 30 days' notice is required to terminate an auto-renewal.",
    ),
    "Warranty Duration": PolicyRule(
        id="deterministic_warranty_duration", rule_type="mandatory", applies_to=["general"],
        severity="MEDIUM", section_reference="Deterministic policy table",
        rule_text="Warranty periods should not exceed 12 months without Legal approval.",
    ),
    "Renewal Term": PolicyRule(
        id="deterministic_renewal_term", rule_type="mandatory", applies_to=["general"],
        severity="MEDIUM", section_reference="Deterministic policy table",
        rule_text="Auto-renewal terms should not exceed 12 months without Legal approval.",
    ),
}


def _extract_multiplier(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|times)\b", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _extract_dollar_amount(text: str) -> Optional[float]:
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", text)
    return float(m.group(1).replace(",", "")) if m else None


def _extract_days(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*days?\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _extract_months(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*(?:months?|mo)\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    y = re.search(r"(\d+)\s*(?:years?|yr)\b", text, re.IGNORECASE)
    if y:
        return int(y.group(1)) * 12
    return None


def _violation(rule: PolicyRule, issue: str) -> Dict[str, Any]:
    return {
        "rule_id": rule.id,
        "issue": issue,
        "severity": rule.severity,
        "suggested_fix": rule.rule_text,
        "confidence": 1.0,
    }


def evaluate_deterministic(clause_type: str, clause_text: str) -> Optional[Dict[str, Any]]:
    """
    Returns a violation dict if clause_type is in the deterministic table,
    a number could be confidently extracted from clause_text, and it fails
    the threshold. Returns None otherwise (not a deterministic category,
    or the number couldn't be parsed - never a guess).
    """
    rule = DETERMINISTIC_RULES.get(clause_type)
    if not rule or not clause_text:
        return None

    if clause_type == "Cap On Liability":
        value = _extract_multiplier(clause_text)
        if value is not None and value > 2:
            return _violation(rule, f"Liability cap of {value}x fees exceeds the 2x policy maximum.")
        return None

    if clause_type == "Minimum Commitment":
        value = _extract_dollar_amount(clause_text)
        if value is not None and value > 100000:
            return _violation(rule, f"Minimum commitment of ${value:,.0f} exceeds the $100,000 policy threshold.")
        return None

    if clause_type == "Notice Period To Terminate Renewal":
        value = _extract_days(clause_text)
        if value is not None and value < 30:
            return _violation(rule, f"Notice period of {value} days is below the 30-day policy minimum.")
        return None

    if clause_type in ("Warranty Duration", "Renewal Term"):
        value = _extract_months(clause_text)
        if value is not None and value > 12:
            return _violation(rule, f"{clause_type} of {value} months exceeds the 12-month policy maximum.")
        return None

    return None
