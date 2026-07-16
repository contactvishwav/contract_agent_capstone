from langchain.tools import BaseTool
from typing import Dict, List, Any, Optional
import json
import logging
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.agents.cuad_mitigation_tools import DeviationDetectorTool, JurisdictionAdapterTool, PrecedentMatcherTool

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class EnhancedDeviationDetectorTool(DeviationDetectorTool):
    """Enhanced deviation detection with semantic analysis - Phase 2"""
    
    name: str = "enhanced_deviation_detector"
    description: str = "Advanced deviation detection using semantic analysis and ML patterns"
    
    def __init__(self):
        super().__init__()
        # Use object.__setattr__ to bypass Pydantic validation
        object.__setattr__(self, 'semantic_patterns', self._load_semantic_patterns())
    
    def _run(self, clauses_json: str) -> str:
        """Enhanced deviation analysis with semantic understanding"""
        try:
            clauses = json.loads(clauses_json)
            deviations = []
            
            for clause in clauses:
                # Run basic pattern matching first
                basic_deviation = self._check_clause_deviation(clause.get("clause_type", ""), clause.get("content", ""))
                if basic_deviation:
                    deviations.append({
                        **basic_deviation,
                        "clause_type": clause.get("clause_type", ""),
                        "clause_content": clause.get("content", "")[:200] + "..." if len(clause.get("content", "")) > 200 else clause.get("content", ""),
                        "detection_method": "pattern_matching"
                    })
                
                # Enhanced semantic analysis
                semantic_deviations = self._semantic_deviation_analysis(clause)
                deviations.extend(semantic_deviations)
            
            return json.dumps(deviations)
            
        except Exception as e:
            logger.error(f"Enhanced deviation detection failed: {e}")
            return json.dumps([])
    
    def _semantic_deviation_analysis(self, clause: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Semantic analysis for complex deviations"""
        content = clause.get("content", "").lower()
        clause_type = clause.get("clause_type", "").lower()
        deviations = []
        
        # Semantic patterns for complex deviations
        for pattern_name, pattern_config in self.semantic_patterns.items():
            if self._matches_semantic_pattern(content, clause_type, pattern_config):
                deviations.append({
                    "clause_type": clause.get("clause_type", ""),
                    "deviation_type": pattern_name,
                    "severity": pattern_config["severity"],
                    "issue": pattern_config["issue"],
                    "suggested_fix": pattern_config["fix"],
                    "clause_content": clause.get("content", "")[:200] + "..." if len(clause.get("content", "")) > 200 else clause.get("content", ""),
                    "detection_method": "semantic_analysis",
                    "confidence_score": pattern_config.get("confidence", 0.8)
                })
        
        return deviations
    
    def _load_semantic_patterns(self) -> Dict[str, Dict[str, Any]]:
        """Load semantic deviation patterns"""
        return {
            "hidden_auto_renewal": {
                "keywords": ["automatically renew", "auto-renewal", "unless terminated", "perpetual"],
                "clause_types": ["termination", "term", "renewal"],
                "severity": "HIGH",
                "issue": "Contract contains hidden auto-renewal clause",
                "fix": "Add explicit termination notice requirements",
                "confidence": 0.85
            },
            "broad_indemnification": {
                "keywords": ["indemnify", "hold harmless", "defend", "all claims", "any claims"],
                "clause_types": ["indemnification", "liability", "indemnity"],
                "severity": "CRITICAL",
                "issue": "Overly broad indemnification obligations",
                "fix": "Limit indemnification to specific breach scenarios",
                "confidence": 0.9
            },
            "data_retention_risk": {
                "keywords": ["retain data", "store information", "indefinitely", "permanent storage"],
                "clause_types": ["data protection", "privacy", "confidentiality"],
                "severity": "HIGH",
                "issue": "Unlimited data retention period",
                "fix": "Add specific data retention and deletion timelines",
                "confidence": 0.8
            },
            "exclusive_dealing": {
                "keywords": ["exclusively", "sole", "only provider", "exclusive rights"],
                "clause_types": ["exclusivity", "scope", "services"],
                "severity": "MEDIUM",
                "issue": "Exclusive dealing arrangement detected",
                "fix": "Consider non-exclusive alternatives or time limits",
                "confidence": 0.75
            }
        }
    
    def _matches_semantic_pattern(self, content: str, clause_type: str, pattern: Dict[str, Any]) -> bool:
        """Check if content matches semantic pattern"""
        # Check if clause type matches
        if pattern.get("clause_types") and not any(ct in clause_type for ct in pattern["clause_types"]):
            return False
        
        # Check keyword presence (require at least 2 keywords for higher confidence)
        keyword_matches = sum(1 for keyword in pattern["keywords"] if keyword in content)
        return keyword_matches >= 2

class EnhancedJurisdictionAdapterTool(JurisdictionAdapterTool):
    """Enhanced jurisdiction adaptation with industry-specific rules - Phase 2"""
    
    name: str = "enhanced_jurisdiction_adapter"
    description: str = "Advanced jurisdiction detection with industry-specific rule adaptation"
    
    def __init__(self):
        super().__init__()
        # Use object.__setattr__ to bypass Pydantic validation
        object.__setattr__(self, 'industry_rules', self._load_industry_rules())
    
    def _run(self, contract_text: str, industry: str = None) -> str:
        """Enhanced jurisdiction adaptation with industry context"""
        try:
            # Basic jurisdiction detection
            jurisdiction = self._detect_jurisdiction(contract_text)
            industry_detected = industry or self._detect_industry(contract_text)
            
            # Get base jurisdiction rules
            base_rules = self._get_jurisdiction_rules(jurisdiction)
            
            # Apply industry-specific adaptations
            adapted_rules = self._apply_industry_adaptations(base_rules, jurisdiction, industry_detected)
            
            # Get enhanced compliance requirements
            compliance_requirements = self._get_enhanced_compliance_requirements(jurisdiction, industry_detected)
            
            return json.dumps({
                "jurisdiction": jurisdiction,
                "industry": industry_detected,
                "adapted_rules": adapted_rules,
                "compliance_requirements": compliance_requirements,
                "risk_factors": self._assess_jurisdiction_risks(jurisdiction, industry_detected)
            })
            
        except Exception as e:
            logger.error(f"Enhanced jurisdiction adaptation failed: {e}")
            return json.dumps({
                "jurisdiction": "unknown", 
                "industry": "unknown",
                "adapted_rules": {}, 
                "compliance_requirements": [],
                "risk_factors": []
            })
    
    def _detect_industry(self, contract_text: str) -> str:
        """Detect contract industry from text patterns"""
        text_lower = contract_text.lower()
        
        # Healthcare indicators
        if any(term in text_lower for term in ["hipaa", "phi", "medical", "healthcare", "patient data", "baa"]):
            return "healthcare"
        
        # Financial services indicators
        if any(term in text_lower for term in ["pci dss", "financial", "banking", "sox", "payment card"]):
            return "financial"
        
        # Defense/Government indicators
        if any(term in text_lower for term in ["itar", "export control", "classified", "security clearance", "government"]):
            return "defense"
        
        # Technology indicators
        if any(term in text_lower for term in ["software", "saas", "api", "cloud", "data processing"]):
            return "technology"
        
        return "general"
    
    def _apply_industry_adaptations(self, base_rules: Dict[str, Any], jurisdiction: str, industry: str) -> Dict[str, Any]:
        """Apply industry-specific rule adaptations"""
        adapted_rules = dict(base_rules)
        
        industry_adaptations = self.industry_rules.get(industry, {})
        jurisdiction_adaptations = industry_adaptations.get(jurisdiction, {})
        
        # Merge industry-specific rules
        for category, rules in jurisdiction_adaptations.items():
            if category in adapted_rules:
                adapted_rules[category].update(rules)
            else:
                adapted_rules[category] = rules
        
        return adapted_rules
    
    def _load_industry_rules(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Load industry-specific jurisdiction rules"""
        return {
            "healthcare": {
                "US": {
                    "data_protection": {
                        "hipaa_required": True,
                        "baa_mandatory": True,
                        "breach_notification_timeline": "60 days"
                    },
                    "liability": {
                        "malpractice_insurance_required": True,
                        "minimum_coverage": "$1M"
                    }
                },
                "EU": {
                    "data_protection": {
                        "gdpr_health_data": True,
                        "explicit_consent_required": True,
                        "dpo_required": True
                    }
                }
            },
            "financial": {
                "US": {
                    "data_protection": {
                        "pci_dss_required": True,
                        "sox_compliance": True,
                        "ffiec_guidelines": True
                    },
                    "audit": {
                        "annual_audit_required": True,
                        "penetration_testing": "quarterly"
                    }
                }
            },
            "defense": {
                "US": {
                    "export_control": {
                        "itar_compliance": True,
                        "ear_compliance": True,
                        "security_clearance_required": True
                    },
                    "data_protection": {
                        "nist_800_171": True,
                        "cmmc_required": True
                    }
                }
            }
        }
    
    def _get_enhanced_compliance_requirements(self, jurisdiction: str, industry: str) -> List[str]:
        """Get enhanced compliance requirements"""
        base_requirements = self._get_compliance_requirements(jurisdiction)
        
        industry_requirements = {
            "healthcare": [
                "HIPAA compliance for PHI handling",
                "Business Associate Agreement required",
                "Breach notification procedures mandatory"
            ],
            "financial": [
                "PCI DSS compliance for payment data",
                "SOX compliance if publicly traded",
                "Regular security audits required"
            ],
            "defense": [
                "ITAR compliance for defense articles",
                "NIST 800-171 security controls",
                "CMMC certification required"
            ]
        }
        
        return base_requirements + industry_requirements.get(industry, [])
    
    def _assess_jurisdiction_risks(self, jurisdiction: str, industry: str) -> List[str]:
        """Assess jurisdiction-specific risks"""
        risks = []
        
        if jurisdiction == "EU" and industry == "technology":
            risks.append("GDPR fines up to 4% of global revenue")
            risks.append("Right to be forgotten compliance required")
        
        if jurisdiction == "US" and industry == "healthcare":
            risks.append("HIPAA violations can result in criminal charges")
            risks.append("State breach notification laws vary")
        
        if jurisdiction == "US" and industry == "defense":
            risks.append("ITAR violations can result in export license suspension")
            risks.append("Criminal penalties for willful violations")
        
        return risks

class EnhancedPrecedentMatcherTool(PrecedentMatcherTool):
    """Enhanced precedent matching with real database integration - Phase 2"""
    
    name: str = "enhanced_precedent_matcher"
    description: str = "Advanced precedent matching using vector similarity and real contract database"
    
    def __init__(self):
        super().__init__()
        # Use object.__setattr__ to bypass Pydantic validation
        object.__setattr__(self, 'repository', Neo4jContractRepository())
    
    def _run(self, clauses_json: str, tenant_id: str = "demo_tenant_1") -> str:
        """Enhanced precedent matching with real database - Enforces multi-tenancy"""
        try:
            clauses = json.loads(clauses_json)
            matches = []
            
            for clause in clauses:
                # Get real precedents from database
                real_precedents = self._find_real_precedents(clause, tenant_id)
                
                if real_precedents:
                    analysis = self._analyze_precedents(real_precedents, clause)
                    matches.append({
                        "clause": clause,
                        "precedent_count": len(real_precedents),
                        "approval_rate": analysis["approval_rate"],
                        "risk_patterns": analysis["risk_patterns"],
                        "recommendations": analysis["recommendations"],
                        "similar_contracts": analysis["similar_contracts"],
                        "trend_analysis": analysis["trend_analysis"]
                    })
                else:
                    # Fallback to mock data if no real precedents
                    fallback_precedents = self._find_similar_clauses(clause)
                    if fallback_precedents:
                        matches.append({
                            "clause": clause,
                            "precedent_count": len(fallback_precedents),
                            "approval_rate": self._calculate_approval_rate(fallback_precedents),
                            "risk_patterns": self._identify_risk_patterns(fallback_precedents),
                            "recommendations": self._generate_recommendations(fallback_precedents),
                            "similar_contracts": [],
                            "trend_analysis": {"note": "Limited historical data available"}
                        })
            
            return json.dumps(matches)
            
        except Exception as e:
            logger.error(f"Enhanced precedent matching failed: {e}")
            return json.dumps([])
    
    def _find_real_precedents(self, clause: Dict[str, Any], tenant_id: str) -> List[Dict[str, Any]]:
        """Find real precedents from contract database with tenant-level isolation"""
        try:
            clause_type = clause.get("clause_type", "").lower()
            clause_content = clause.get("content", "")
            
            # Query similar clauses from Neo4j - Multi-tenant enabled
            query = """
            MATCH (c:Contract {tenant_id: $tenant_id})-[:CONTAINS_CLAUSE]->(cl:Clause)
            WHERE toLower(cl.clause_type) CONTAINS $clause_type
            AND c.intelligence_status = 'completed'
            RETURN cl.clause_type as type,
                   cl.content as content,
                   cl.risk_level as risk_level,
                   c.risk_score as contract_risk,
                   c.file_id as contract_id,
                   c.contract_type as contract_type
            LIMIT 10
            """
            
            results = self.repository.graph.query(query, {
                "clause_type": clause_type,
                "tenant_id": tenant_id
            })
            
            precedents = []
            for result in results:
                # Simple similarity check (in production, use vector embeddings)
                similarity = self._calculate_text_similarity(clause_content, result.get("content", ""))
                if similarity > 0.3:  # Threshold for relevance
                    precedents.append({
                        "clause_type": result.get("type"),
                        "content": result.get("content"),
                        "risk_level": result.get("risk_level", "UNKNOWN"),
                        "contract_risk": result.get("contract_risk", 0),
                        "contract_id": result.get("contract_id"),
                        "contract_type": result.get("contract_type"),
                        "similarity_score": similarity,
                        "approved": result.get("risk_level") in ["LOW", "MEDIUM"]  # Assume low/medium risk = approved
                    })
            
            return sorted(precedents, key=lambda x: x["similarity_score"], reverse=True)
            
        except Exception as e:
            logger.error(f"Real precedent search failed: {e}")
            return []
    
    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """Simple text similarity calculation (placeholder for vector similarity)"""
        if not text1 or not text2:
            return 0.0
        
        # Simple word overlap similarity
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
    
    def _analyze_precedents(self, precedents: List[Dict[str, Any]], clause: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze precedents for patterns and recommendations"""
        if not precedents:
            return {
                "approval_rate": 0.0,
                "risk_patterns": [],
                "recommendations": [],
                "similar_contracts": [],
                "trend_analysis": {}
            }
        
        # Calculate approval rate
        approved_count = sum(1 for p in precedents if p.get("approved", False))
        approval_rate = approved_count / len(precedents)
        
        # Identify risk patterns
        risk_patterns = []
        high_risk_count = sum(1 for p in precedents if p.get("risk_level") in ["HIGH", "CRITICAL"])
        if high_risk_count > len(precedents) * 0.4:
            risk_patterns.append("High proportion of similar clauses flagged as high risk")
        
        avg_contract_risk = sum(p.get("contract_risk", 0) for p in precedents) / len(precedents)
        if avg_contract_risk > 70:
            risk_patterns.append("Similar clauses associated with high-risk contracts")
        
        # Generate recommendations
        recommendations = []
        if approval_rate < 0.5:
            recommendations.append("Consider revising clause - low approval rate in similar contracts")
        if avg_contract_risk > 60:
            recommendations.append("Review clause carefully - associated with higher risk contracts")
        if approval_rate > 0.8:
            recommendations.append("Clause appears acceptable based on precedent analysis")
        
        # Similar contracts
        similar_contracts = [
            {
                "contract_id": p.get("contract_id"),
                "contract_type": p.get("contract_type"),
                "similarity_score": p.get("similarity_score"),
                "risk_level": p.get("risk_level")
            }
            for p in precedents[:5]  # Top 5 most similar
        ]
        
        # Trend analysis
        trend_analysis = {
            "total_precedents": len(precedents),
            "average_similarity": sum(p.get("similarity_score", 0) for p in precedents) / len(precedents),
            "risk_distribution": {
                "low": sum(1 for p in precedents if p.get("risk_level") == "LOW"),
                "medium": sum(1 for p in precedents if p.get("risk_level") == "MEDIUM"),
                "high": sum(1 for p in precedents if p.get("risk_level") == "HIGH"),
                "critical": sum(1 for p in precedents if p.get("risk_level") == "CRITICAL")
            }
        }
        
        return {
            "approval_rate": approval_rate,
            "risk_patterns": risk_patterns,
            "recommendations": recommendations,
            "similar_contracts": similar_contracts,
            "trend_analysis": trend_analysis
        }