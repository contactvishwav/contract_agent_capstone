"""Chain-of-Thought Pattern Agent - Explicit step-by-step reasoning with SOLID principles."""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from .base_pattern_agent import BasePatternAgent
from backend.agents.intelligence_tools import PolicyCheckerTool, RiskCalculatorTool
import json
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


@dataclass
class ThoughtStep:
    step_number: int
    description: str
    input_data: Dict[str, Any]
    output_data: Dict[str, Any]
    reasoning: str
    confidence: float


class ChainOfThoughtAgent(BasePatternAgent):
    """Chain-of-Thought: Explicit step-by-step reasoning (SOLID: SRP, DIP)"""
    
    def __init__(self):
        super().__init__("Chain-of-Thought Pattern Agent")
        self.thought_chain: List[ThoughtStep] = []
        self.policy_tool = PolicyCheckerTool()  # Reuse existing (DRY)
        self.risk_tool = RiskCalculatorTool()  # Reuse existing (DRY)
    
    def get_agent_role(self) -> str:
        return "Step-by-step reasoning for contract risk assessment"
    
    def get_pattern_name(self) -> str:
        return "Chain-of-Thought"
    
    async def _execute_pattern(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """CoT-specific logic reusing existing tools (DRY principle)"""
        task_type = context.get('task_type', 'risk_assessment')
        
        if task_type == 'risk_assessment':
            return await self._risk_assessment_chain(context)
        elif task_type == 'clause_analysis':
            return await self._clause_analysis_chain(context)
        else:
            return {'success': False, 'error': f'Unknown task type: {task_type}'}
    
    async def _risk_assessment_chain(self, context: Dict[str, Any]) -> Dict[str, Any]:
        clauses = context.get('clauses', [])
        tenant_id = context.get('tenant_id', 'default')
        contract_type = context.get('contract_type', 'general')
        
        # Step 1: Identify clauses
        step1 = await self._add_thought_step(
            1, "Identify Contract Clauses",
            {'clauses_count': len(clauses)},
            {'identified_clauses': [c.get('type', 'unknown') for c in clauses]},
            f"Found {len(clauses)} clauses in contract for analysis",
            0.9
        )
        
        # Step 2: Load dynamic policies from database
        from backend.infrastructure.policy_repository import PolicyRepository
        policy_repo = PolicyRepository()
        policies = policy_repo.get_applicable_policies(tenant_id, contract_type)
        
        step2 = await self._add_thought_step(
            2, "Load Applicable Policies",
            {'tenant_id': tenant_id, 'contract_type': contract_type},
            {'policies_loaded': len(policies)},
            f"Loaded {len(policies)} applicable policies from database for tenant {tenant_id}",
            0.9
        )
        
        # Step 3: Check policy compliance using dynamic policies
        violations = []
        for clause in clauses:
            clause_content = clause.get('content', '').lower()
            clause_type = clause.get('type', 'general')
            
            # Check against loaded policies
            for policy in policies:
                if clause_type in policy.applies_to or 'general' in policy.applies_to:
                    violation = self._check_dynamic_policy_compliance(clause, policy)
                    if violation:
                        violations.append(violation)
        
        step3 = await self._add_thought_step(
            3, "Check Policy Compliance",
            {'policies_checked': len(policies), 'clauses_analyzed': len(clauses)},
            {'violations_found': len(violations), 'violations': violations},
            f"Checked {len(clauses)} clauses against {len(policies)} dynamic policies, found {len(violations)} violations",
            0.85 if violations else 0.95
        )
        
        # Step 4: Generate recommendations
        recommendations = []
        for violation in violations:
            recommendations.append({
                'clause_type': violation['clause_type'],
                'recommendation': self._generate_recommendation(violation),
                'priority': violation['severity']
            })
        
        step4 = await self._add_thought_step(
            4, "Generate Recommendations",
            {'violations': len(violations)},
            {'recommendations': recommendations},
            f"Generated {len(recommendations)} recommendations based on violations",
            0.8
        )
        
        # Step 5: Final assessment
        # Overall risk is driven by the most severe violation found (a single
        # CRITICAL violation should dominate the assessment, not be averaged
        # away by unrelated LOW-severity ones). No violations -> baseline risk.
        overall_risk = max(
            (self._severity_to_risk_score(v['severity']) for v in violations),
            default=1,
        )
        final_confidence = sum(step.confidence for step in self.thought_chain) / len(self.thought_chain)

        step5 = await self._add_thought_step(
            5, "Final Risk Assessment",
            {'overall_risk': overall_risk, 'recommendations_count': len(recommendations)},
            {'final_risk_score': overall_risk, 'confidence': final_confidence},
            f"Final assessment: Risk={overall_risk}/10, Confidence={final_confidence:.2f}",
            final_confidence
        )
        
        return {
            'success': True,
            'pattern': self.get_pattern_name(),
            'thought_chain': [self._step_to_dict(step) for step in self.thought_chain],
            'final_result': {
                'risk_score': overall_risk,
                'violations': violations,
                'recommendations': recommendations,
                'confidence': final_confidence
            }
        }
    
    async def _clause_analysis_chain(self, context: Dict[str, Any]) -> Dict[str, Any]:
        contract_text = context.get('contract_text', '')
        target_clause = context.get('target_clause', '')
        
        # Step 1: Parse contract structure
        sections = self._parse_contract_sections(contract_text)
        
        step1 = await self._add_thought_step(
            1, "Parse Contract Structure",
            {'contract_length': len(contract_text), 'target': target_clause},
            {'sections_found': len(sections)},
            f"Parsed contract into {len(sections)} sections for analysis",
            0.9
        )
        
        # Step 2: Locate target clause
        clause_locations = []
        for i, section in enumerate(sections):
            if target_clause.lower() in section.lower():
                clause_locations.append({
                    'section_index': i,
                    'section_preview': section[:100] + '...',
                    'relevance_score': self._calculate_relevance(section, target_clause)
                })
        
        step2 = await self._add_thought_step(
            2, "Locate Target Clause",
            {'target_clause': target_clause, 'sections_searched': len(sections)},
            {'locations_found': len(clause_locations), 'locations': clause_locations},
            f"Found {len(clause_locations)} potential locations for '{target_clause}'",
            0.8 if clause_locations else 0.3
        )
        
        # Step 3: Extract and analyze clause content
        extracted_clauses = []
        for location in clause_locations:
            section = sections[location['section_index']]
            clause_content = self._extract_clause_content(section, target_clause)
            extracted_clauses.append({
                'content': clause_content,
                'section_index': location['section_index'],
                'key_terms': self._extract_key_terms(clause_content)
            })
        
        step3 = await self._add_thought_step(
            3, "Extract Clause Content",
            {'locations': len(clause_locations)},
            {'extracted_clauses': len(extracted_clauses)},
            f"Extracted {len(extracted_clauses)} clause instances with key terms",
            0.85 if extracted_clauses else 0.2
        )
        
        # Step 4: Final analysis
        final_confidence = sum(step.confidence for step in self.thought_chain) / len(self.thought_chain)
        
        step4 = await self._add_thought_step(
            4, "Complete Analysis",
            {'clauses_analyzed': len(extracted_clauses)},
            {'final_confidence': final_confidence},
            f"Analysis complete with {final_confidence:.2f} confidence",
            final_confidence
        )
        
        return {
            'success': True,
            'pattern': self.get_pattern_name(),
            'thought_chain': [self._step_to_dict(step) for step in self.thought_chain],
            'final_result': {
                'extracted_clauses': extracted_clauses,
                'confidence': final_confidence
            }
        }
    
    async def _add_thought_step(self, step_number: int, description: str, 
                              input_data: Dict[str, Any], output_data: Dict[str, Any],
                              reasoning: str, confidence: float) -> ThoughtStep:
        step = ThoughtStep(step_number, description, input_data, output_data, reasoning, confidence)
        self.thought_chain.append(step)
        return step
    
    def _check_dynamic_policy_compliance(self, clause: Dict[str, Any], policy) -> Optional[Dict[str, Any]]:
        """Check clause compliance against dynamic policy from database."""
        clause_content = clause.get('content', '').lower()
        rule_text = policy.rule_text.lower()
        
        if policy.rule_type == 'prohibited':
            # Check for prohibited terms
            prohibited_terms = ['unlimited liability', 'immediate termination', 'no notice']
            for term in prohibited_terms:
                if term in clause_content and term in rule_text:
                    return {
                        'clause_type': clause.get('type', 'unknown'),
                        'violation': f"Clause contains prohibited term: {term}",
                        'severity': policy.severity,
                        'policy_rule_id': policy.id
                    }
        
        elif policy.rule_type == 'mandatory':
            # Check for missing mandatory terms
            mandatory_terms = ['liability cap', 'notice period', 'governing law']
            for term in mandatory_terms:
                if term in rule_text and term not in clause_content:
                    return {
                        'clause_type': clause.get('type', 'unknown'),
                        'violation': f"Clause missing mandatory term: {term}",
                        'severity': policy.severity,
                        'policy_rule_id': policy.id
                    }
        
        return None
    
    def _get_violation_reason(self, clause: Dict[str, Any], policy: Dict[str, Any]) -> str:
        clause_content = clause.get('content', '').lower()
        
        if 'liability' in policy.get('type', '') and 'unlimited' in clause_content:
            return "Unlimited liability violates company policy"
        elif 'termination' in policy.get('type', '') and 'immediate' in clause_content:
            return "Immediate termination violates notice period policy"
        
        return "Policy violation detected"
    
    def _calculate_severity(self, clause: Dict[str, Any], policy: Dict[str, Any]) -> str:
        clause_content = clause.get('content', '').lower()
        
        if 'unlimited' in clause_content or 'immediate' in clause_content:
            return 'CRITICAL'
        elif 'high' in clause_content or 'significant' in clause_content:
            return 'HIGH'
        else:
            return 'MEDIUM'
    
    def _severity_to_risk_score(self, severity: str) -> int:
        severity_map = {'CRITICAL': 9, 'HIGH': 7, 'MEDIUM': 5, 'LOW': 3}
        return severity_map.get(severity, 1)
    
    def _generate_recommendation(self, violation: Dict[str, Any]) -> str:
        clause_type = violation['clause_type']
        severity = violation['severity']
        
        if 'liability' in clause_type.lower():
            return "Add liability cap of $1M to limit financial exposure"
        elif 'termination' in clause_type.lower():
            return "Add 30-day notice period for termination"
        
        return f"Review and modify {clause_type} clause to ensure policy compliance"
    
    def _parse_contract_sections(self, contract_text: str) -> List[str]:
        # Simple section parsing
        sections = []
        current_section = ""
        
        for line in contract_text.split('\n'):
            if line.strip() and (line.isupper() or line.startswith('Section') or line.startswith('Article')):
                if current_section:
                    sections.append(current_section)
                current_section = line + '\n'
            else:
                current_section += line + '\n'
        
        if current_section:
            sections.append(current_section)
        
        return sections
    
    def _calculate_relevance(self, section: str, target_clause: str) -> float:
        section_lower = section.lower()
        target_lower = target_clause.lower()
        
        if target_lower in section_lower:
            return 1.0
        
        # Check for related terms
        related_terms = {
            'liability': ['damages', 'responsibility', 'obligation'],
            'termination': ['end', 'expire', 'cancel'],
            'payment': ['invoice', 'fee', 'cost']
        }
        
        score = 0.0
        for key, terms in related_terms.items():
            if key in target_lower:
                for term in terms:
                    if term in section_lower:
                        score += 0.2
        
        return min(score, 1.0)
    
    def _extract_clause_content(self, section: str, target_clause: str) -> str:
        lines = section.split('\n')
        relevant_lines = []
        
        for line in lines:
            if target_clause.lower() in line.lower():
                relevant_lines.append(line.strip())
        
        return ' '.join(relevant_lines) if relevant_lines else section[:200]
    
    def _extract_key_terms(self, clause_content: str) -> List[str]:
        # Simple key term extraction
        legal_keywords = ['shall', 'must', 'required', 'obligation', 'rights', 'liability', 'damages', 'termination']
        found_terms = []
        
        content_lower = clause_content.lower()
        for keyword in legal_keywords:
            if keyword in content_lower:
                found_terms.append(keyword)
        
        return found_terms
    
    def _step_to_dict(self, step: ThoughtStep) -> Dict[str, Any]:
        return {
            'step_number': step.step_number,
            'description': step.description,
            'input_data': step.input_data,
            'output_data': step.output_data,
            'reasoning': step.reasoning,
            'confidence': step.confidence
        }