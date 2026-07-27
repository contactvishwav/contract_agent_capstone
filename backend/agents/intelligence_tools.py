from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, Dict, Any, List, Optional
from backend.domain.entities import ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.agents.llm_extraction_service import LLMExtractionService, get_default_llm
import json
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Company policy rules - merged existing with comprehensive internal policies
COMPANY_POLICIES = {
    "payment_terms": {
        "preferred_days": 30,
        "acceptable_days": 45,  # requires Delivery Director approval
        "red_flags": [60, 90],
        "redline_text": "Payment is due within thirty (30) days of invoice receipt."
    },
    "liability_cap": {
        "preferred_multiplier": 1,  # 1x total fees
        "acceptable_multiplier": 2,  # requires Legal approval
        "min_amount": 100000,  # legacy minimum
        "red_flags": ["unlimited", "indirect_damages", "consequential_damages"],
        "redline_text": "Our liability shall not exceed the total fees paid or payable under the applicable Statement of Work."
    },
    "indemnification": {
        "preferred_type": "mutual",
        "acceptable_scope": ["third_party_ip", "gross_negligence", "willful_misconduct"],
        "red_flags": ["broad_indemnification", "client_negligence", "open_ended_defense"],
        "redline_text": "Each party will indemnify the other solely for third-party claims arising from gross negligence, willful misconduct, or infringement of IP under this Agreement."
    },
    "termination": {
        "min_notice_days": 30,
        "payment_required": "work_in_progress",
        "red_flags": ["immediate_termination", "no_payment_wip"],
        "redline_text": "Either party may terminate this SOW with thirty (30) days' written notice. All completed work shall be payable upon termination."
    },
    "ip_ownership": {
        "company_retains": "pre_existing_ip",
        "client_owns": "deliverables",
        "red_flags": ["client_claims_company_ip", "assignment_without_carveouts"],
        "redline_text": "Client owns deliverables created specifically for the engagement. Company retains ownership of its pre-existing IP, reusable tools, and methodologies."
    },
    "confidentiality": {
        "required": True,
        "mutual": True,
        "redline_text": "Both parties agree to maintain confidentiality of all proprietary information."
    }
}

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

    def _run(self, contract_text: str) -> str:
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

            clauses = [
                {
                    "clause_type": e.clause_type.value,
                    "content": e.extracted_text,
                    "confidence_score": e.confidence,
                    "start_offset": e.start_offset,
                    "end_offset": e.end_offset,
                }
                for e in extracted
            ]

            logger.info(f"Extracted {len(clauses)} clauses")
            return json.dumps(clauses)

        except Exception as e:
            logger.error(f"Clause detection failed: {e}")
            return json.dumps([])

# Policy Compliance Agent Tools
class PolicyCheckerInput(BaseModel):
    clauses_json: str = Field(description="JSON string of extracted clauses")

class PolicyCheckerTool(BaseTool):
    name: str = "policy_checker"
    description: str = "Check clauses against company policies"
    args_schema: Type[BaseModel] = PolicyCheckerInput
    
    def _run(self, clauses_json: str) -> str:
        """Check clauses against policies"""
        try:
            clauses = json.loads(clauses_json)
            violations = []
            
            for clause in clauses:
                clause_type = clause.get("clause_type", "").lower()
                content = clause.get("content", "").lower()
                
                # Check payment terms against company policy
                if "payment" in clause_type:
                    if any(term in content for term in ["60 days", "90 days", "net 60", "net 90"]):
                        violations.append({
                            "clause_type": clause["clause_type"],
                            "issue": "Payment terms exceed company policy (Net 30 preferred, Net 45 max with approval)",
                            "severity": "CRITICAL",
                            "suggested_fix": COMPANY_POLICIES["payment_terms"]["redline_text"],
                            "clause_content": clause["content"]
                        })
                    elif any(term in content for term in ["45 days", "net 45"]):
                        violations.append({
                            "clause_type": clause["clause_type"],
                            "issue": "Payment terms require Delivery Director approval (Net 45)",
                            "severity": "MEDIUM",
                            "suggested_fix": "Obtain Delivery Director approval or " + COMPANY_POLICIES["payment_terms"]["redline_text"],
                            "clause_content": clause["content"]
                        })
                
                # Check liability caps against company policy
                if "liability" in clause_type:
                    if any(term in content for term in ["unlimited", "indirect", "consequential", "special damages"]):
                        violations.append({
                            "clause_type": clause["clause_type"],
                            "issue": "Liability policy violation - unlimited or indirect/consequential damages exposure",
                            "severity": "CRITICAL",
                            "suggested_fix": COMPANY_POLICIES["liability_cap"]["redline_text"],
                            "clause_content": clause["content"]
                        })
                    elif any(amount in content for amount in ["50,000", "25,000", "$50k", "$25k"]):
                        violations.append({
                            "clause_type": clause["clause_type"],
                            "issue": "Liability cap not linked to SOW fees and below minimum threshold",
                            "severity": "HIGH",
                            "suggested_fix": COMPANY_POLICIES["liability_cap"]["redline_text"],
                            "clause_content": clause["content"]
                        })
                
                # Check indemnification against company policy
                if "indemnif" in clause_type.lower() or "indemnit" in content:
                    if any(term in content for term in ["broad", "client negligence", "misuse", "open-ended"]):
                        violations.append({
                            "clause_type": "Indemnification",
                            "issue": "Broad indemnification or client negligence coverage violates company policy",
                            "severity": "CRITICAL",
                            "suggested_fix": COMPANY_POLICIES["indemnification"]["redline_text"],
                            "clause_content": clause["content"]
                        })
                
                # Check termination against company policy
                if "terminat" in clause_type.lower():
                    if any(term in content for term in ["immediate", "no notice", "0 days"]):
                        violations.append({
                            "clause_type": clause["clause_type"],
                            "issue": "Immediate termination without notice violates company policy",
                            "severity": "HIGH",
                            "suggested_fix": COMPANY_POLICIES["termination"]["redline_text"],
                            "clause_content": clause["content"]
                        })
                
                # Check IP ownership against company policy
                if "ip" in clause_type.lower() or "intellectual property" in clause_type.lower():
                    if any(term in content for term in ["client owns all", "assignment of rights", "company ip to client"]):
                        violations.append({
                            "clause_type": clause["clause_type"],
                            "issue": "IP assignment without carve-outs for company pre-existing IP",
                            "severity": "CRITICAL",
                            "suggested_fix": COMPANY_POLICIES["ip_ownership"]["redline_text"],
                            "clause_content": clause["content"]
                        })
            
            logger.info(f"Found {len(violations)} policy violations")
            return json.dumps(violations)
            
        except Exception as e:
            logger.error(f"Policy checking failed: {e}")
            return json.dumps([])

# Risk Assessment Agent Tools
class RiskCalculatorInput(BaseModel):
    clauses_json: str = Field(description="JSON string of clauses")
    violations_json: str = Field(description="JSON string of violations")

class RiskCalculatorTool(BaseTool):
    name: str = "risk_calculator"
    description: str = "Calculate overall contract risk score"
    args_schema: Type[BaseModel] = RiskCalculatorInput
    
    def _run(self, clauses_json: str, violations_json: str) -> str:
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
            
            critical_issues = [v["issue"] for v in violations if v.get("severity") == "CRITICAL"]
            
            assessment = {
                "overall_risk_score": risk_score,
                "risk_level": risk_level,
                "critical_issues": critical_issues,
                "recommendations": recommendations
            }
            
            logger.info(f"Risk assessment: {risk_level} ({risk_score}/100)")
            return json.dumps(assessment)
            
        except Exception as e:
            logger.error(f"Risk calculation failed: {e}")
            return json.dumps({"overall_risk_score": 50.0, "risk_level": "MEDIUM", "critical_issues": [], "recommendations": []})

# Redline Generation Agent Tools
class RedlineGeneratorInput(BaseModel):
    violations_json: str = Field(description="JSON string of policy violations")

class RedlineGeneratorTool(BaseTool):
    name: str = "redline_generator"
    description: str = "Generate redline recommendations for violations"
    args_schema: Type[BaseModel] = RedlineGeneratorInput
    
    def _run(self, violations_json: str) -> str:
        """Generate redline recommendations"""
        try:
            violations = json.loads(violations_json)
            redlines = []
            
            for violation in violations:
                clause_type = violation.get("clause_type", "")
                issue = violation.get("issue", "")
                suggested_fix = violation.get("suggested_fix", "")
                original_text = violation.get("clause_content", "")
                
                if "payment" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["payment_terms"]["redline_text"],
                        "justification": "Aligns with company payment policy (Net 30 preferred)",
                        "priority": "HIGH"
                    })
                
                elif "liability" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["liability_cap"]["redline_text"],
                        "justification": "Caps liability at 1x SOW fees per company policy",
                        "priority": "CRITICAL"
                    })
                
                elif "indemnif" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["indemnification"]["redline_text"],
                        "justification": "Limits indemnification to mutual third-party claims only",
                        "priority": "CRITICAL"
                    })
                
                elif "terminat" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["termination"]["redline_text"],
                        "justification": "Ensures 30-day notice and payment for work-in-progress",
                        "priority": "HIGH"
                    })
                
                elif "ip" in clause_type.lower() or "intellectual property" in clause_type.lower():
                    redlines.append({
                        "original_text": original_text,
                        "suggested_text": COMPANY_POLICIES["ip_ownership"]["redline_text"],
                        "justification": "Protects company pre-existing IP and methodologies",
                        "priority": "CRITICAL"
                    })
            
            logger.info(f"Generated {len(redlines)} redline recommendations")
            return json.dumps(redlines)
            
        except Exception as e:
            logger.error(f"Redline generation failed: {e}")
            return json.dumps([])