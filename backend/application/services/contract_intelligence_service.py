from backend.agents.contract_intelligence_agents import ContractIntelligenceAgentFactory
from backend.domain.entities import ContractIntelligence, ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.llm_manager import LLMManager
import json
import logging
import time
from typing import Dict, Any, Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class ContractIntelligenceService:
    """Service for contract intelligence analysis using multi-agent system"""
    
    def __init__(self, llm_manager: LLMManager):
        self.llm_manager = llm_manager
        self.repository = Neo4jContractRepository()
    
    def analyze_contract_intelligence(self, contract_text: str, model: str = "gemini-2.5-flash", use_planning: bool = True, contract_id: Optional[str] = None, tenant_id: Optional[str] = None, contract_type: Optional[str] = None) -> ContractIntelligence:
        """Perform complete contract intelligence analysis using multi-agent system"""

        start_time = time.time()

        try:
            logger.info(f"Starting contract intelligence analysis with model: {model}")

            # Get LLM for the specified model
            llm = self._get_llm_for_model(model)

            # Create multi-agent orchestrator with error handling
            try:
                orchestrator = ContractIntelligenceAgentFactory.create_orchestrator(llm)
                # Run multi-agent analysis with optional planning
                analysis_result = orchestrator.analyze_contract(contract_text, use_planning, contract_id=contract_id, tenant_id=tenant_id, contract_type=contract_type)
            except ImportError as ie:
                logger.error(f"Import error in orchestrator: {ie}")
                raise Exception(f"Intelligence system not properly configured: {ie}")
            except Exception as oe:
                logger.error(f"Orchestrator creation failed: {oe}")
                raise Exception(f"Failed to initialize intelligence system: {oe}")
            
            # Convert to domain entities
            intelligence = self._convert_to_domain_entities(analysis_result)
            intelligence.processing_time = time.time() - start_time
            
            logger.info(f"Contract intelligence analysis completed in {intelligence.processing_time:.2f}s")
            return intelligence
            
        except Exception as e:
            logger.error(f"Contract intelligence analysis failed: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # Return empty result on failure
            return ContractIntelligence(
                clauses=[],
                violations=[],
                risk_assessment=RiskAssessment(
                    overall_risk_score=0.0,
                    risk_level="UNKNOWN",
                    critical_issues=[],
                    recommendations=["Analysis failed - manual review required"]
                ),
                redlines=[],
                processing_time=time.time() - start_time,
                processing_complete=False
            )
    
    async def analyze_contract_by_id(self, contract_id: str, tenant_id: str = "default-tenant", model: str = "gemini-2.5-flash", use_planning: bool = True) -> Optional[ContractIntelligence]:
        """Analyze contract intelligence for an existing contract by ID"""
        
        try:
            # Get contract text from database
            contract_data = await self.repository.get_contract_by_id(contract_id, tenant_id)
            
            if not contract_data:
                logger.error(f"Contract not found: {contract_id}")
                return None
            
            # Use full text if available, otherwise fallback to summary
            contract_text = contract_data.get("full_text", "")
            logger.info(f"Contract {contract_id}: full_text length = {len(contract_text)}")
            
            if not contract_text.strip():
                contract_text = contract_data.get("summary", "") + " " + contract_data.get("contract_scope", "")
                logger.info(f"Contract {contract_id}: fallback text length = {len(contract_text)}")
            
            if not contract_text.strip():
                logger.error(f"No text content found for contract: {contract_id}")
                logger.error(f"Contract data keys: {list(contract_data.keys())}")
                return None

            contract_type = contract_data.get("contract_type") or "general"

            # Perform analysis with optional planning
            intelligence = self.analyze_contract_intelligence(contract_text, model, use_planning, contract_id=contract_id, tenant_id=tenant_id, contract_type=contract_type)
            
            # Store intelligence results back to database
            self._store_intelligence_results(contract_id, tenant_id, intelligence)
            
            return intelligence
            
        except Exception as e:
            logger.error(f"Failed to analyze contract {contract_id}: {e}")
            return None
    
    def _get_llm_for_model(self, model: str):
        """Get LLM instance for the specified model"""
        try:
            return self.llm_manager.agents[model]._llm if hasattr(self.llm_manager.agents[model], '_llm') else self.llm_manager.agents[model]
        except KeyError:
            logger.warning(f"Model {model} not found, using default")
            # Use first available model as fallback
            available_models = list(self.llm_manager.agents.keys())
            if available_models:
                fallback_model = available_models[0]
                return self.llm_manager.agents[fallback_model]._llm if hasattr(self.llm_manager.agents[fallback_model], '_llm') else self.llm_manager.agents[fallback_model]
            else:
                raise ValueError("No LLM models available")
    
    def _convert_to_domain_entities(self, analysis_result: Dict[str, Any]) -> ContractIntelligence:
        """Convert analysis results to domain entities"""
        
        # Convert clauses
        clauses = []
        for clause_data in analysis_result.get("clauses", []):
            clauses.append(ContractClause(
                clause_type=clause_data.get("clause_type", ""),
                content=clause_data.get("content", ""),
                risk_level=clause_data.get("risk_level", "LOW"),
                confidence_score=clause_data.get("confidence_score", 0.0),
                location=clause_data.get("location", ""),
                clause_id=clause_data.get("clause_id", ""),
                grounded=clause_data.get("grounded", True),
                original_risk_level=clause_data.get("original_risk_level"),
                learned_risk_adjustment=clause_data.get("learned_risk_adjustment"),
                pattern_confidence=clause_data.get("pattern_confidence"),
                risk_adjustment_pattern_id=clause_data.get("risk_adjustment_pattern_id"),
            ))

        # Convert violations
        violations = []
        for violation_data in analysis_result.get("violations", []):
            violations.append(PolicyViolation(
                clause_type=violation_data.get("clause_type", ""),
                issue=violation_data.get("issue", ""),
                severity=violation_data.get("severity", "LOW"),
                suggested_fix=violation_data.get("suggested_fix", ""),
                clause_content=violation_data.get("clause_content", ""),
                clause_id=violation_data.get("clause_id", ""),
                clause_grounded=violation_data.get("clause_grounded", True)
            ))

        # Convert risk assessment
        risk_data = analysis_result.get("risk_assessment", {})
        risk_assessment = RiskAssessment(
            overall_risk_score=risk_data.get("overall_risk_score", 0.0),
            risk_level=risk_data.get("risk_level", "LOW"),
            critical_issues=risk_data.get("critical_issues", []),
            recommendations=risk_data.get("recommendations", []),
            critical_issue_details=risk_data.get("critical_issue_details", [])
        )
        
        # Convert redlines
        redlines = []
        for redline_data in analysis_result.get("redlines", []):
            redlines.append(RedlineRecommendation(
                original_text=redline_data.get("original_text", ""),
                suggested_text=redline_data.get("suggested_text", ""),
                justification=redline_data.get("justification", ""),
                priority=redline_data.get("priority", "LOW")
            ))
        
        # Create ContractIntelligence with CUAD data
        intelligence = ContractIntelligence(
            clauses=clauses,
            violations=violations,
            risk_assessment=risk_assessment,
            redlines=redlines
        )
        
        # Add CUAD fields if present
        intelligence.cuad_deviations = analysis_result.get("cuad_deviations", [])
        intelligence.jurisdiction_info = analysis_result.get("jurisdiction_info", {})
        intelligence.precedent_matches = analysis_result.get("precedent_matches", [])

        # Honest partial-failure state: which nodes/steps actually succeeded
        intelligence.node_status = analysis_result.get("node_status", {})
        intelligence.processing_complete = analysis_result.get("processing_complete", True)

        # Supervisor rebuild: quality grade, escalation flag, and which
        # CUAD mitigation tier actually ran - see PlanExecutionEngine.
        # _format_final_results for where these are computed. Only the
        # planning path (PlanExecutionEngine) currently produces these;
        # the traditional-workflow path's analysis_result simply won't
        # have these keys, so quality_grade/analysis_method fall back to
        # their dataclass defaults ({}/None) rather than fabricating one.
        intelligence.quality_grade = analysis_result.get("quality_grade", {})
        intelligence.escalated = analysis_result.get("escalated", False)
        intelligence.analysis_method = analysis_result.get("analysis_method")
        
        return intelligence
    
    def _store_intelligence_results(self, contract_id: str, tenant_id: str, intelligence: ContractIntelligence):
        """Store intelligence analysis results in the database"""
        
        try:
            # Update contract with intelligence data including CUAD fields
            intelligence_data = {
                "risk_score": intelligence.risk_assessment.overall_risk_score,
                "risk_level": intelligence.risk_assessment.risk_level,
                "violations_count": len(intelligence.violations),
                "clauses_count": len(intelligence.clauses),
                "redlines_count": len(intelligence.redlines),
                # Honest partial-failure state: don't persist "completed" if
                # node_status/processing_complete say some or all of the
                # analysis didn't actually run (e.g. policy checking hit a
                # quota/network error on some clauses) - GET .../status reads
                # this field straight back, so a caller polling status must
                # not see a false "completed" for a result with real gaps.
                "intelligence_status": "completed" if intelligence.processing_complete else "completed_with_errors",
                "processing_time": intelligence.processing_time,
                # CUAD-specific fields
                "cuad_analysis_status": "completed",
                "deviation_count": len(intelligence.cuad_deviations),
                "jurisdiction_detected": intelligence.jurisdiction_info.get("jurisdiction", "unknown"),
                "industry_detected": intelligence.jurisdiction_info.get("industry", "general"),
                "precedent_matches": len(intelligence.precedent_matches),
                "semantic_analysis_enabled": True,
                "cache_enabled": True,
                "performance_optimized": True
            }
            
            # Store in Neo4j with CUAD fields
            query = """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            SET c.risk_score = $risk_score,
                c.risk_level = $risk_level,
                c.violations_count = $violations_count,
                c.clauses_count = $clauses_count,
                c.redlines_count = $redlines_count,
                c.intelligence_status = $intelligence_status,
                c.processing_time = $processing_time,
                c.cuad_analysis_status = $cuad_analysis_status,
                c.deviation_count = $deviation_count,
                c.jurisdiction_detected = $jurisdiction_detected,
                c.industry_detected = $industry_detected,
                c.precedent_matches = $precedent_matches,
                c.semantic_analysis_enabled = $semantic_analysis_enabled,
                c.cache_enabled = $cache_enabled,
                c.performance_optimized = $performance_optimized,
                c.intelligence_updated = datetime()
            RETURN c
            """
            
            self.repository.graph.query(query, {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                **intelligence_data
            })
            
            # Store performance metrics
            self._store_performance_metrics(contract_id, tenant_id, intelligence)
            
            logger.info(f"Stored intelligence results for contract: {contract_id}")
            
        except Exception as e:
            logger.error(f"Failed to store intelligence results for {contract_id}: {e}")
    
    def _store_performance_metrics(self, contract_id: str, tenant_id: str, intelligence: ContractIntelligence):
        """Store performance metrics in database"""
        try:
            # Get validation result if available
            validation_result = getattr(intelligence, 'validation_result', None)
            
            # Store performance metric
            metric_query = """
            CREATE (pm:PerformanceMetric {
                metric_id: randomUUID(),
                contract_id: $contract_id,
                tenant_id: $tenant_id,
                operation: 'cuad_analysis',
                duration_ms: $duration_ms,
                success: $success,
                timestamp: datetime(),
                phase_used: 'phase3',
                validation_score: $validation_score,
                deviation_count: $deviation_count,
                jurisdiction: $jurisdiction
            })
            """
            
            self.repository.graph.query(metric_query, {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                "duration_ms": intelligence.processing_time * 1000,
                "success": True,
                "validation_score": validation_result.confidence_score if validation_result else 0.0,
                "deviation_count": len(intelligence.cuad_deviations),
                "jurisdiction": intelligence.jurisdiction_info.get("jurisdiction", "unknown")
            })
            
        except Exception as e:
            logger.warning(f"Failed to store performance metrics: {e}")

class ContractIntelligenceServiceFactory:
    """Factory for creating contract intelligence service"""
    
    @staticmethod
    def create_service(llm_manager: LLMManager) -> ContractIntelligenceService:
        """Create a new contract intelligence service"""
        return ContractIntelligenceService(llm_manager)