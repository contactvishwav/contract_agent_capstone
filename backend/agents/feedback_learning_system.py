from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import json
import logging
from backend.infrastructure.contract_repository import Neo4jContractRepository

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Clause types with structurally significant legal/financial exposure
# regardless of whether a specific policy rule flags them - e.g. an
# Uncapped Liability clause is inherently notable even if no configured
# policy rule technically "violates" on it. Everything not listed here or
# in _INHERENT_LOW_RISK_CLAUSE_TYPES defaults to MEDIUM.
_INHERENT_HIGH_RISK_CLAUSE_TYPES = {
    "Uncapped Liability", "Non-Compete", "Exclusivity", "Ip Ownership Assignment",
    "Termination For Convenience", "Change Of Control", "Minimum Commitment",
    "Unlimited/All-You-Can-Eat-License", "Source Code Escrow",
    "Post-Termination Services", "Covenant Not To Sue",
}
# Metadata/administrative clause types with no substantive legal exposure
# of their own.
_INHERENT_LOW_RISK_CLAUSE_TYPES = {
    "Document Name", "Parties", "Agreement Date", "Effective Date",
    "Expiration Date", "Governing Law", "Renewal Term",
    "Notice Period To Terminate Renewal",
}

_SEVERITY_TO_RISK_LEVEL = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}


def compute_baseline_risk_level(clause: Dict[str, Any], violations: List[Dict[str, Any]]) -> str:
    """
    A real, non-constant risk_level per clause, computed before any learned-
    pattern adjustment is applied on top of it (AdaptiveAnalyzer._apply_risk_
    pattern needs a genuine baseline to override, not the previous silent
    "LOW" default that nothing ever actually computed -
    ClauseDetectorTool._run never set risk_level at all).

    Primary signal: the max severity among any policy violations already
    found for this exact clause (matched by clause_id) - CHECK_POLICIES
    always runs before CUAD_MITIGATION in both plan templates (planning_
    agent.py), so this is real, contract-specific, already-computed data,
    not a guess. Falls back to a clause-type inherent-risk category only
    when no violation exists for this clause - some clause types carry
    real risk just by being present (e.g. Uncapped Liability), even in a
    contract where nothing about them technically violates a configured
    policy rule.
    """
    clause_id = clause.get("clause_id")
    matching_severities = [
        v.get("severity", "LOW") for v in violations if clause_id and v.get("clause_id") == clause_id
    ]
    if matching_severities:
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if level in matching_severities:
                return _SEVERITY_TO_RISK_LEVEL[level]

    clause_type = clause.get("clause_type", "")
    if clause_type in _INHERENT_HIGH_RISK_CLAUSE_TYPES:
        return "HIGH"
    if clause_type in _INHERENT_LOW_RISK_CLAUSE_TYPES:
        return "LOW"
    return "MEDIUM"


@dataclass
class LegalDecision:
    """Legal team decision on contract analysis"""
    decision_id: str
    contract_id: str
    clause_id: str
    clause_type: str
    original_analysis: Dict[str, Any]
    legal_decision: str  # "approved", "rejected", "modified"
    legal_feedback: str
    risk_assessment_override: Optional[str] = None
    confidence_score: float = 0.0
    decision_timestamp: datetime = None
    
    def __post_init__(self):
        if self.decision_timestamp is None:
            self.decision_timestamp = datetime.now()

@dataclass
class FeedbackPattern:
    """Pattern learned from legal team feedback"""
    pattern_id: str
    pattern_type: str  # "deviation", "risk", "approval"
    conditions: Dict[str, Any]
    learned_outcome: str
    confidence: float
    usage_count: int = 0
    success_rate: float = 0.0

class FeedbackCollector:
    """Collect and store legal team decisions"""
    
    def __init__(self):
        self.repository = Neo4jContractRepository()
    
    def collect_decision(self, decision: LegalDecision) -> None:
        """Store legal team decision"""
        try:
            # Store decision in Neo4j
            query = """
            MERGE (d:LegalDecision {decision_id: $decision_id})
            SET d.contract_id = $contract_id,
                d.clause_id = $clause_id,
                d.clause_type = $clause_type,
                d.original_analysis = $original_analysis,
                d.legal_decision = $legal_decision,
                d.legal_feedback = $legal_feedback,
                d.risk_assessment_override = $risk_assessment_override,
                d.confidence_score = $confidence_score,
                d.decision_timestamp = $decision_timestamp
            
            // Link to contract
            WITH d
            MATCH (c:Contract {file_id: $contract_id})
            MERGE (c)-[:HAS_DECISION]->(d)
            
            RETURN d
            """
            
            self.repository.graph.query(query, {
                "decision_id": decision.decision_id,
                "contract_id": decision.contract_id,
                "clause_id": decision.clause_id,
                "clause_type": decision.clause_type,
                "original_analysis": json.dumps(decision.original_analysis),
                "legal_decision": decision.legal_decision,
                "legal_feedback": decision.legal_feedback,
                "risk_assessment_override": decision.risk_assessment_override,
                "confidence_score": decision.confidence_score,
                "decision_timestamp": decision.decision_timestamp.isoformat()
            })
            
            logger.info(f"Stored legal decision: {decision.decision_id}")
            
        except Exception as e:
            logger.error(f"Failed to store legal decision: {e}")
    
    def get_decisions_by_clause_type(self, clause_type: str, limit: int = 50) -> List[LegalDecision]:
        """Get legal decisions for specific clause type"""
        try:
            query = """
            MATCH (d:LegalDecision)
            WHERE d.clause_type = $clause_type
            RETURN d
            ORDER BY d.decision_timestamp DESC
            LIMIT $limit
            """
            
            results = self.repository.graph.query(query, {
                "clause_type": clause_type,
                "limit": limit
            })
            
            decisions = []
            for result in results:
                d = result["d"]
                decisions.append(LegalDecision(
                    decision_id=d.get("decision_id"),
                    contract_id=d.get("contract_id"),
                    clause_id=d.get("clause_id"),
                    clause_type=d.get("clause_type"),
                    original_analysis=json.loads(d.get("original_analysis", "{}")),
                    legal_decision=d.get("legal_decision"),
                    legal_feedback=d.get("legal_feedback"),
                    risk_assessment_override=d.get("risk_assessment_override"),
                    confidence_score=d.get("confidence_score", 0.0),
                    decision_timestamp=datetime.fromisoformat(d.get("decision_timestamp"))
                ))
            
            return decisions
            
        except Exception as e:
            logger.error(f"Failed to get decisions for clause type {clause_type}: {e}")
            return []

class PatternLearner:
    """Learn patterns from legal team feedback"""
    
    def __init__(self):
        self.collector = FeedbackCollector()
        self.learned_patterns: List[FeedbackPattern] = []
    
    def learn_from_decisions(self, clause_type: str) -> List[FeedbackPattern]:
        """Learn patterns from legal decisions"""
        decisions = self.collector.get_decisions_by_clause_type(clause_type)
        
        if len(decisions) < 5:  # Need minimum decisions to learn patterns
            return []
        
        patterns = []
        
        # Learn approval patterns
        approved_decisions = [d for d in decisions if d.legal_decision == "approved"]
        if approved_decisions:
            approval_pattern = self._extract_approval_pattern(approved_decisions, clause_type)
            if approval_pattern:
                patterns.append(approval_pattern)
        
        # Learn rejection patterns
        rejected_decisions = [d for d in decisions if d.legal_decision == "rejected"]
        if rejected_decisions:
            rejection_pattern = self._extract_rejection_pattern(rejected_decisions, clause_type)
            if rejection_pattern:
                patterns.append(rejection_pattern)
        
        # Learn risk assessment patterns
        override_decisions = [d for d in decisions if d.risk_assessment_override]
        if override_decisions:
            risk_pattern = self._extract_risk_pattern(override_decisions, clause_type)
            if risk_pattern:
                patterns.append(risk_pattern)
        
        self.learned_patterns.extend(patterns)
        return patterns
    
    def _extract_approval_pattern(self, decisions: List[LegalDecision], clause_type: str) -> Optional[FeedbackPattern]:
        """Extract common patterns from approved decisions"""
        if len(decisions) < 3:
            return None
        
        # Analyze common characteristics of approved clauses
        common_keywords = self._find_common_keywords([d.original_analysis for d in decisions])
        common_risk_levels = self._find_common_risk_levels([d.original_analysis for d in decisions])
        
        if common_keywords or common_risk_levels:
            return FeedbackPattern(
                pattern_id=f"approval_{clause_type}_{datetime.now().strftime('%Y%m%d')}",
                pattern_type="approval",
                conditions={
                    "clause_type": clause_type,
                    "common_keywords": common_keywords,
                    "acceptable_risk_levels": common_risk_levels
                },
                learned_outcome="likely_approved",
                confidence=len(decisions) / 10.0,  # Confidence based on sample size
                usage_count=0,
                success_rate=0.0
            )
        
        return None
    
    def _extract_rejection_pattern(self, decisions: List[LegalDecision], clause_type: str) -> Optional[FeedbackPattern]:
        """Extract common patterns from rejected decisions"""
        if len(decisions) < 3:
            return None
        
        # Analyze common reasons for rejection
        rejection_keywords = []
        for decision in decisions:
            feedback_words = decision.legal_feedback.lower().split()
            rejection_keywords.extend(feedback_words)
        
        # Find most common rejection reasons
        keyword_counts = {}
        for keyword in rejection_keywords:
            if len(keyword) > 3:  # Filter short words
                keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1
        
        common_rejection_reasons = [k for k, v in keyword_counts.items() if v >= 2]
        
        if common_rejection_reasons:
            return FeedbackPattern(
                pattern_id=f"rejection_{clause_type}_{datetime.now().strftime('%Y%m%d')}",
                pattern_type="rejection",
                conditions={
                    "clause_type": clause_type,
                    "rejection_indicators": common_rejection_reasons
                },
                learned_outcome="likely_rejected",
                confidence=len(decisions) / 10.0,
                usage_count=0,
                success_rate=0.0
            )
        
        return None
    
    def _extract_risk_pattern(self, decisions: List[LegalDecision], clause_type: str) -> Optional[FeedbackPattern]:
        """Extract risk assessment override patterns"""
        if len(decisions) < 2:
            return None
        
        # Analyze risk assessment overrides
        override_patterns = {}
        for decision in decisions:
            original_risk = decision.original_analysis.get("risk_level", "UNKNOWN")
            override_risk = decision.risk_assessment_override
            
            pattern_key = f"{original_risk}_to_{override_risk}"
            override_patterns[pattern_key] = override_patterns.get(pattern_key, 0) + 1
        
        # Find most common override pattern
        most_common_override = max(override_patterns.items(), key=lambda x: x[1])
        
        if most_common_override[1] >= 2:  # At least 2 occurrences
            return FeedbackPattern(
                pattern_id=f"risk_override_{clause_type}_{datetime.now().strftime('%Y%m%d')}",
                pattern_type="risk_override",
                conditions={
                    "clause_type": clause_type,
                    "override_pattern": most_common_override[0]
                },
                learned_outcome=f"risk_adjustment_{most_common_override[0]}",
                confidence=most_common_override[1] / len(decisions),
                usage_count=0,
                success_rate=0.0
            )
        
        return None
    
    def _find_common_keywords(self, analyses: List[Dict[str, Any]]) -> List[str]:
        """Find common keywords across analyses"""
        all_keywords = []
        for analysis in analyses:
            content = analysis.get("content", "").lower()
            all_keywords.extend(content.split())
        
        # Count keyword frequency
        keyword_counts = {}
        for keyword in all_keywords:
            if len(keyword) > 4:  # Filter short words
                keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1
        
        # Return keywords that appear in at least 50% of analyses
        threshold = len(analyses) * 0.5
        return [k for k, v in keyword_counts.items() if v >= threshold]
    
    def _find_common_risk_levels(self, analyses: List[Dict[str, Any]]) -> List[str]:
        """Find common risk levels in approved analyses"""
        risk_levels = [analysis.get("risk_level", "UNKNOWN") for analysis in analyses]
        risk_counts = {}
        for level in risk_levels:
            risk_counts[level] = risk_counts.get(level, 0) + 1
        
        # Return risk levels that appear in at least 30% of analyses
        threshold = len(analyses) * 0.3
        return [k for k, v in risk_counts.items() if v >= threshold]

class AdaptiveAnalyzer:
    """Apply learned patterns to improve analysis"""
    
    def __init__(self):
        self.pattern_learner = PatternLearner()
        self.active_patterns: Dict[str, List[FeedbackPattern]] = {}
    
    def load_patterns_for_clause_type(self, clause_type: str) -> None:
        """Load learned patterns for specific clause type"""
        patterns = self.pattern_learner.learn_from_decisions(clause_type)
        self.active_patterns[clause_type] = patterns
        logger.info(f"Loaded {len(patterns)} patterns for clause type: {clause_type}")
    
    def enhance_analysis(self, clause: Dict[str, Any], original_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Enhance analysis using learned patterns"""
        clause_type = clause.get("clause_type", "")
        
        # Load patterns if not already loaded
        if clause_type not in self.active_patterns:
            self.load_patterns_for_clause_type(clause_type)
        
        patterns = self.active_patterns.get(clause_type, [])
        enhanced_analysis = dict(original_analysis)
        
        # Apply learned patterns
        for pattern in patterns:
            enhancement = self._apply_pattern(clause, original_analysis, pattern)
            if enhancement:
                enhanced_analysis.update(enhancement)
        
        return enhanced_analysis
    
    def _apply_pattern(self, clause: Dict[str, Any], analysis: Dict[str, Any], pattern: FeedbackPattern) -> Optional[Dict[str, Any]]:
        """Apply a specific learned pattern"""
        try:
            if pattern.pattern_type == "approval":
                return self._apply_approval_pattern(clause, analysis, pattern)
            elif pattern.pattern_type == "rejection":
                return self._apply_rejection_pattern(clause, analysis, pattern)
            elif pattern.pattern_type == "risk_override":
                return self._apply_risk_pattern(clause, analysis, pattern)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to apply pattern {pattern.pattern_id}: {e}")
            return None
    
    def _apply_approval_pattern(self, clause: Dict[str, Any], analysis: Dict[str, Any], pattern: FeedbackPattern) -> Optional[Dict[str, Any]]:
        """Apply approval pattern"""
        conditions = pattern.conditions
        content = clause.get("content", "").lower()
        
        # Check if clause matches approval pattern
        common_keywords = conditions.get("common_keywords", [])
        keyword_matches = sum(1 for keyword in common_keywords if keyword in content)
        
        if keyword_matches >= len(common_keywords) * 0.5:  # 50% keyword match
            return {
                "learned_approval_likelihood": "high",
                "pattern_confidence": pattern.confidence,
                "matching_keywords": [k for k in common_keywords if k in content]
            }
        
        return None
    
    def _apply_rejection_pattern(self, clause: Dict[str, Any], analysis: Dict[str, Any], pattern: FeedbackPattern) -> Optional[Dict[str, Any]]:
        """Apply rejection pattern"""
        conditions = pattern.conditions
        content = clause.get("content", "").lower()
        
        # Check for rejection indicators
        rejection_indicators = conditions.get("rejection_indicators", [])
        indicator_matches = [indicator for indicator in rejection_indicators if indicator in content]
        
        if indicator_matches:
            return {
                "learned_rejection_risk": "high",
                "rejection_indicators_found": indicator_matches,
                "pattern_confidence": pattern.confidence
            }
        
        return None
    
    def _apply_risk_pattern(self, clause: Dict[str, Any], analysis: Dict[str, Any], pattern: FeedbackPattern) -> Optional[Dict[str, Any]]:
        """
        Apply a risk override pattern - actually changes the risk_level a
        client sees (not just a sibling field nothing reads), while keeping
        it traceable: original_risk_level preserves what the computed
        baseline was before this pattern applied (see
        compute_baseline_risk_level), and learned_risk_adjustment/
        pattern_confidence/risk_adjustment_pattern_id let a reviewer see
        that - and why - this specific clause's risk was adjusted, matching
        the clause_id/rule_id traceability already established for
        violations (P1).
        """
        conditions = pattern.conditions
        current_risk = analysis.get("risk_level", "UNKNOWN")

        override_pattern = conditions.get("override_pattern", "")
        if override_pattern.startswith(f"{current_risk}_to_"):
            suggested_risk = override_pattern.split("_to_")[1]
            return {
                "risk_level": suggested_risk,
                "original_risk_level": current_risk,
                "learned_risk_adjustment": suggested_risk,
                "pattern_confidence": pattern.confidence,
                "risk_adjustment_pattern_id": pattern.pattern_id,
            }

        return None