"""
Default policy rule set used when a tenant hasn't uploaded any policy
document of their own (PolicyRepository.get_policies_by_tenant returns
none). Deliberately small - not meant to be exhaustive - but real: sourced,
genuine compliance positions for the highest-risk CUAD categories, not a
no-op/empty fallback that silently skips evaluation for tenants without an
upload.

Every id is prefixed "default_" so a violation citing one is visibly
distinct from a rule sourced from a tenant's own uploaded policy - a legal
reviewer can tell at a glance whether a flagged issue reflects the
tenant's actual stated policy or this generic baseline.
"""

from backend.domain.policies.entities import PolicyRule

DEFAULT_POLICY_RULES = [
    PolicyRule(
        id="default_uncapped_liability", rule_type="prohibited", applies_to=["general"],
        severity="CRITICAL", section_reference="Default policy set",
        rule_text="Liability must not be uncapped/unlimited, or extend to indirect, "
                  "special, or consequential damages.",
    ),
    PolicyRule(
        id="default_indemnification_scope", rule_type="prohibited", applies_to=["general"],
        severity="CRITICAL", section_reference="Default policy set",
        rule_text="Indemnification must be mutual and limited to third-party IP "
                  "infringement, gross negligence, or willful misconduct - not broad, "
                  "one-sided, or open-ended.",
    ),
    PolicyRule(
        id="default_termination_notice", rule_type="mandatory", applies_to=["general"],
        severity="HIGH", section_reference="Default policy set",
        rule_text="Termination for convenience must require at least 30 days' written "
                  "notice, with payment for work already completed.",
    ),
    PolicyRule(
        id="default_ip_assignment_carveout", rule_type="mandatory", applies_to=["general"],
        severity="CRITICAL", section_reference="Default policy set",
        rule_text="IP assignment must carve out each party's pre-existing IP and "
                  "reusable tools/methodologies - the counterparty should own only "
                  "deliverables created specifically for the engagement.",
    ),
    PolicyRule(
        id="default_non_compete_scope", rule_type="recommended", applies_to=["general"],
        severity="MEDIUM", section_reference="Default policy set",
        rule_text="Non-compete restrictions should be reasonably limited in duration "
                  "(generally no more than 12-24 months) and in geographic/product scope.",
    ),
    PolicyRule(
        id="default_exclusivity_scope", rule_type="recommended", applies_to=["general"],
        severity="MEDIUM", section_reference="Default policy set",
        rule_text="Exclusivity commitments should be time-bound and tied to minimum "
                  "performance/volume commitments from the counterparty, not open-ended.",
    ),
    PolicyRule(
        id="default_assignment_consent", rule_type="mandatory", applies_to=["general"],
        severity="MEDIUM", section_reference="Default policy set",
        rule_text="Assignment of the agreement, including via change of control, should "
                  "require prior written consent rather than being freely permitted.",
    ),
    PolicyRule(
        id="default_audit_rights_reasonable", rule_type="recommended", applies_to=["general"],
        severity="LOW", section_reference="Default policy set",
        rule_text="Audit rights should be limited to reasonable frequency (e.g. annually), "
                  "business hours, and reasonable advance notice.",
    ),
]


def filter_default_rules_by_contract_type(contract_type: str):
    """Same applies_to filtering convention as PolicyRepository.get_applicable_policies."""
    contract_type = contract_type or "general"
    return [
        r for r in DEFAULT_POLICY_RULES
        if contract_type in r.applies_to or "general" in r.applies_to
    ]
