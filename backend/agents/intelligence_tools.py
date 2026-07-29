from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, Dict, Any, List, Optional
from backend.domain.entities import ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.agents.llm_extraction_service import LLMExtractionService, get_default_llm
from backend.agents.policy_evaluation_service import PolicyEvaluationService
from backend.agents.policy_rule_resolver import get_applicable_rules
from backend.agents.deterministic_policy_rules import evaluate_deterministic
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType
import json
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


def _make_clause_id(contract_id: Optional[str], clause_type: str, start_offset: int, dup_index: int = 0) -> str:
    """
    Deterministic clause id, scoped to the contract and derived from the
    clause's own type/position - NOT a random uuid4. Re-running extraction on
    the same contract must produce the same ids for the same clauses so
    downstream references (violations, risk factors) stay stable across
    re-analysis, matching the id convention already used in
    clause_extraction_agent.py (f"{section_id}_clause_{order:03d}").
    """
    slug = clause_type.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    base = f"{contract_id or 'unknown'}_{slug}_{start_offset}"
    return base if dup_index == 0 else f"{base}_dup{dup_index}"

# Clause Extraction Agent Tools
class ClauseDetectorInput(BaseModel):
    contract_text: str = Field(description="Contract text to analyze for clauses")

class ClauseDetectorTool(BaseTool):
    name: str = "clause_detector"
    description: str = "Detect and extract key contract clauses"
    args_schema: Type[BaseModel] = ClauseDetectorInput

    def __init__(self, llm: Optional[Any] = None):
        super().__init__()
        # Use object.__setattr__ to bypass Pydantic validation (existing
        # convention in this codebase, e.g. EnhancedPrecedentMatcherTool)
        object.__setattr__(self, '_llm', llm)

    def _run(self, contract_text: str, contract_id: Optional[str] = None, tenant_id: Optional[str] = None) -> str:
        """Extract clauses from contract text using real LLM-based extraction"""
        try:
            # No truncation: gemini-2.5-flash supports over 1M input tokens,
            # and clauses like Expiration Date often appear late in long
            # contracts - an arbitrary character cap here would silently
            # drop them before the model ever sees them.

            # Resolve the LLM lazily so construction never requires credentials
            # (this tool is constructed eagerly in several places regardless
            # of whether extraction is ever actually invoked).
            llm = self._llm or get_default_llm()
            service = LLMExtractionService(llm)
            extracted = service.extract_clauses(contract_text)

            seen: Dict[str, int] = {}
            clauses = []
            for e in extracted:
                base_id = _make_clause_id(contract_id, e.clause_type.value, e.start_offset)
                dup_index = seen.get(base_id, 0)
                seen[base_id] = dup_index + 1
                clause_id = _make_clause_id(contract_id, e.clause_type.value, e.start_offset, dup_index)
                # start_offset == -1 means _find_span couldn't locate the
                # LLM's extracted_text anywhere in the source contract text -
                # i.e. the model may have paraphrased or hallucinated the
                # clause rather than quoting it verbatim. Surface that
                # distinction rather than treating it as an equally-verified
                # result: downstream (violations/risk) still gets to see and
                # act on the clause, but a human reviewer can tell it apart.
                clauses.append({
                    "clause_id": clause_id,
                    "clause_type": e.clause_type.value,
                    "content": e.extracted_text,
                    "confidence_score": e.confidence,
                    "start_offset": e.start_offset,
                    "end_offset": e.end_offset,
                    "grounded": e.start_offset != -1,
                })

            ungrounded_count = sum(1 for c in clauses if not c["grounded"])
            logger.info(f"Extracted {len(clauses)} clauses ({ungrounded_count} ungrounded)")
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="clause_extraction",
                metadata={"clause_count": len(clauses), "ungrounded_count": ungrounded_count},
                status="success",
            )
            return json.dumps(clauses)

        except Exception as e:
            logger.error(f"Clause detection failed: {e}")
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="clause_extraction",
                status="failure",
                error_details=str(e),
            )
            return json.dumps([])

# Policy Compliance Agent Tools
class PolicyCheckerInput(BaseModel):
    clauses_json: str = Field(description="JSON string of extracted clauses")

class PolicyCheckerTool(BaseTool):
    name: str = "policy_checker"
    description: str = "Check clauses against tenant (or default) policy rules"
    args_schema: Type[BaseModel] = PolicyCheckerInput

    def __init__(self, llm: Optional[Any] = None):
        super().__init__()
        object.__setattr__(self, '_llm', llm)

    def _run(
        self,
        clauses_json: str,
        contract_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        contract_type: str = "general",
    ) -> str:
        """
        Check each clause against its applicable policy rules: a small
        deterministic table for the handful of categories with an
        objective numeric threshold (no LLM call), and LLM-based reasoning
        (PolicyEvaluationService) against the tenant's own uploaded policy
        rules - or, if the tenant hasn't uploaded any, a small labeled
        default rule set - for everything else. A clause with no
        applicable rules produces no violation and no LLM call: there is
        nothing to evaluate, so nothing is fabricated.
        """
        try:
            clauses = json.loads(clauses_json)
            violations = []

            applicable_rules = get_applicable_rules(tenant_id, contract_type)
            evaluation_service = PolicyEvaluationService(self._llm or get_default_llm())

            for clause in clauses:
                clause_type = clause.get("clause_type", "")
                content = clause.get("content", "")

                deterministic_violation = evaluate_deterministic(clause_type, content)
                if deterministic_violation:
                    deterministic_violation.update({
                        "clause_type": clause_type,
                        "clause_content": content,
                        "clause_id": clause.get("clause_id", "unknown"),
                        "clause_grounded": clause.get("grounded", True),
                    })
                    violations.append(deterministic_violation)
                    continue

                for v in evaluation_service.evaluate_clause(clause_type, content, applicable_rules):
                    v.update({
                        "clause_type": clause_type,
                        "clause_content": content,
                        "clause_id": clause.get("clause_id", "unknown"),
                        "clause_grounded": clause.get("grounded", True),
                    })
                    violations.append(v)

            logger.info(f"Found {len(violations)} policy violations against {len(applicable_rules)} applicable rules")
            critical_count = len([v for v in violations if v.get("severity") == "CRITICAL"])
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="policy_check",
                metadata={"violation_count": len(violations), "critical_count": critical_count},
                status="success",
            )
            return json.dumps(violations)

        except Exception as e:
            logger.error(f"Policy checking failed: {e}")
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="policy_check",
                status="failure",
                error_details=str(e),
            )
            return json.dumps([])

# Risk Assessment Agent Tools
class RiskCalculatorInput(BaseModel):
    clauses_json: str = Field(description="JSON string of clauses")
    violations_json: str = Field(description="JSON string of violations")

class RiskCalculatorTool(BaseTool):
    name: str = "risk_calculator"
    description: str = "Calculate overall contract risk score"
    args_schema: Type[BaseModel] = RiskCalculatorInput
    
    def _run(self, clauses_json: str, violations_json: str, contract_id: Optional[str] = None, tenant_id: Optional[str] = None) -> str:
        """Calculate risk assessment"""
        try:
            clauses = json.loads(clauses_json)
            violations = json.loads(violations_json)
            
            # Calculate base risk from clauses
            risk_score = 30.0  # Base risk
            
            # Add risk from violations
            for violation in violations:
                severity = violation.get("severity", "LOW")
                if severity == "CRITICAL":
                    risk_score += 25
                elif severity == "HIGH":
                    risk_score += 15
                elif severity == "MEDIUM":
                    risk_score += 10
                else:
                    risk_score += 5
            
            # Cap at 100
            risk_score = min(risk_score, 100.0)
            
            # Determine risk level
            if risk_score >= 80:
                risk_level = "CRITICAL"
            elif risk_score >= 60:
                risk_level = "HIGH"
            elif risk_score >= 40:
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"
            
            # Generate recommendations
            recommendations = []
            if len(violations) > 0:
                recommendations.append("Address policy violations before signing")
            if risk_score > 70:
                recommendations.append("Requires legal review and approval")
            
            critical_violations = [v for v in violations if v.get("severity") == "CRITICAL"]
            critical_issues = [v["issue"] for v in critical_violations]
            critical_issue_details = [
                {
                    "issue": v["issue"],
                    "clause_id": v.get("clause_id"),
                    "clause_type": v.get("clause_type"),
                    "clause_grounded": v.get("clause_grounded", True),
                }
                for v in critical_violations
            ]

            assessment = {
                "overall_risk_score": risk_score,
                "risk_level": risk_level,
                "critical_issues": critical_issues,
                "critical_issue_details": critical_issue_details,
                "recommendations": recommendations
            }
            
            logger.info(f"Risk assessment: {risk_level} ({risk_score}/100)")
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="risk_calculation",
                metadata={"risk_score": risk_score, "risk_level": risk_level},
                status="success",
            )
            return json.dumps(assessment)

        except Exception as e:
            logger.error(f"Risk calculation failed: {e}")
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="risk_calculation",
                status="failure",
                error_details=str(e),
            )
            return json.dumps({
                "overall_risk_score": None,
                "risk_level": "ERROR",
                "critical_issues": [],
                "critical_issue_details": [],
                "recommendations": ["Risk calculation failed - manual review required"],
                "error": str(e),
            })

# Redline Generation Agent Tools
class RedlineGeneratorInput(BaseModel):
    violations_json: str = Field(description="JSON string of policy violations")

class RedlineGeneratorTool(BaseTool):
    name: str = "redline_generator"
    description: str = "Generate redline recommendations for violations"
    args_schema: Type[BaseModel] = RedlineGeneratorInput

    def _run(self, violations_json: str, contract_id: Optional[str] = None, tenant_id: Optional[str] = None) -> str:
        """
        Generate a redline directly from each violation's own suggested_fix/
        issue/severity - not a fixed category lookup. PolicyEvaluationService
        (and the deterministic table) already produce a concrete per-violation
        suggested_fix citing the actual rule that was checked, so this works
        for any of the 41 CUAD categories a violation might reference, not
        just a handful of hardcoded keyword matches.
        """
        try:
            violations = json.loads(violations_json)
            redlines = []

            for violation in violations:
                suggested_fix = violation.get("suggested_fix", "")
                if not suggested_fix:
                    continue
                redlines.append({
                    "original_text": violation.get("clause_content", ""),
                    "suggested_text": suggested_fix,
                    "justification": violation.get("issue", ""),
                    "priority": violation.get("severity", "MEDIUM"),
                    "clause_id": violation.get("clause_id", "unknown"),
                    "clause_grounded": violation.get("clause_grounded", True),
                    "rule_id": violation.get("rule_id"),
                })

            logger.info(f"Generated {len(redlines)} redline recommendations")
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="redline_generation",
                metadata={"redline_count": len(redlines)},
                status="success",
            )
            return json.dumps(redlines)

        except Exception as e:
            logger.error(f"Redline generation failed: {e}")
            AuditLogger().log_event(
                event_type=AuditEventType.AGENT_TOOL_CALL,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id or "demo_tenant_1",
                action="redline_generation",
                status="failure",
                error_details=str(e),
            )
            return json.dumps([])