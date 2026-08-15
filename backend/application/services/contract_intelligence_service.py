from backend.agents.contract_intelligence_agents import ContractIntelligenceAgentFactory
from backend.domain.entities import ContractIntelligence, ContractClause, PolicyViolation, RiskAssessment, RedlineRecommendation
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.infrastructure.encryption import field_encryptor
from backend.application.services.intelligence_result_serializer import intelligence_to_response_dict
from backend.llm_manager import LLMManager
from backend.model_registry import model_spec
import json
import logging
import time
import uuid
from typing import Dict, Any, Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)
ANALYSIS_CONFIG_VERSION = "analysis-plan-v1"


class AnalysisPendingReviewError(Exception):
    """Raised when the traditional-workflow graph paused at human_review_gate
    (Phase 4 HITL) instead of completing. Not a failure - a legitimate,
    honest outcome tasks.py/the API must handle distinctly from both
    success and a real error."""

    def __init__(self, contract_id: Optional[str], risk_level: Optional[str], overall_risk_score: Optional[float]):
        self.contract_id = contract_id
        self.risk_level = risk_level
        self.overall_risk_score = overall_risk_score
        super().__init__(f"Contract {contract_id} analysis paused for human review ({risk_level})")


class ContractIntelligenceService:
    """Service for contract intelligence analysis using multi-agent system"""
    
    def __init__(self, llm_manager: LLMManager):
        self.llm_manager = llm_manager
        self.repository = Neo4jContractRepository()
    
    def analyze_contract_intelligence(self, contract_text: str, model: str = "gemini-2.5-flash", use_planning: bool = False, contract_id: Optional[str] = None, tenant_id: Optional[str] = None, contract_type: Optional[str] = None) -> ContractIntelligence:
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

            if analysis_result.get("status") == "PENDING_HUMAN_REVIEW":
                risk_data = analysis_result.get("risk_assessment", {})
                raise AnalysisPendingReviewError(
                    contract_id=contract_id,
                    risk_level=risk_data.get("risk_level"),
                    overall_risk_score=risk_data.get("overall_risk_score"),
                )

            # Convert to domain entities
            intelligence = self._convert_to_domain_entities(analysis_result)
            intelligence.processing_time = time.time() - start_time
            manager = getattr(self, "llm_manager", None)
            spec = manager.get_model_spec(model) if manager else model_spec(model)
            intelligence.requested_model = model
            intelligence.actual_model = model
            intelligence.requested_provider = spec.provider
            intelligence.actual_provider = spec.provider
            intelligence.fallback_occurred = False
            intelligence.fallback_reason = None
            intelligence.configuration_version = ANALYSIS_CONFIG_VERSION
            
            logger.info(f"Contract intelligence analysis completed in {intelligence.processing_time:.2f}s")
            return intelligence

        except AnalysisPendingReviewError:
            raise
        except Exception as e:
            logger.error(f"Contract intelligence analysis failed: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # A plausible empty legal-analysis object is not a truthful
            # provider failure. Let Celery/API status report FAILURE and never
            # persist requested_model as though it were an actual invocation.
            raise RuntimeError("Contract intelligence provider execution failed") from e
    
    async def analyze_contract_by_id(self, contract_id: str, tenant_id: str = "default-tenant", model: str = "gemini-2.5-flash", use_planning: bool = False) -> Optional[ContractIntelligence]:
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
            self._store_intelligence_results(contract_id, tenant_id, model, intelligence)

            return intelligence

        except AnalysisPendingReviewError as e:
            # Persisted so GET .../status and .../reviews/pending reflect
            # this even after the Celery task result itself expires -
            # thread_id for resume is contract_id, already stable/known.
            self._mark_pending_review(contract_id, tenant_id, model, e.risk_level, e.overall_risk_score)
            raise
        except Exception as e:
            logger.error(f"Failed to analyze contract {contract_id}: {e}")
            raise
    
    def _get_llm_for_model(self, model: str):
        """Get a real, structured-output-capable LLM instance for the
        specified model.

        Real, confirmed bug found live: this used to try
        self.llm_manager.agents[model]._llm, hoping to unwrap a raw chat
        model out of what LLMManager.agents actually stores - the compiled
        Contract Chat LangGraph agent (get_agent(llm) -> builder.compile()),
        which never had a `._llm` attribute. hasattr(...) was always False,
        for every model, so this always fell through to returning the
        compiled graph itself - invisible until intelligence_tools.py's
        tools were actually wired to use the resolved value (a separate,
        since-fixed bug), at which point every real analysis started
        failing with "'CompiledStateGraph' object has no attribute
        'with_structured_output'", for every model, not just non-default
        ones. Fixed by using LLMManager.raw_llms, which keeps the actual
        chat model instance each agent was built from.
        """
        try:
            return self.llm_manager.raw_llms[model]
        except KeyError as exc:
            # Legal analysis must never silently substitute another provider.
            raise ValueError(f"Selected analysis model {model} is unavailable") from exc
    
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
            critical_issue_details=risk_data.get("critical_issue_details", []),
            score_breakdown=risk_data.get("score_breakdown"),
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
        intelligence.execution_path = analysis_result.get("execution_path")
        intelligence.planned_execution = analysis_result.get("planned_execution")
        
        return intelligence
    
    def _store_intelligence_results(self, contract_id: str, tenant_id: str, model: str, intelligence: ContractIntelligence):
        """Atomically store summary fields and the complete replayable result."""
        
        try:
            # Update contract with intelligence data including CUAD fields
            manager = getattr(self, "llm_manager", None)
            spec = manager.get_model_spec(model) if manager else model_spec(model)
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
                "performance_optimized": True,
                "execution_path": intelligence.execution_path,
                "planned_execution": intelligence.planned_execution,
                "analysis_method": intelligence.analysis_method,
                "model_used": model,
                "requested_model": model,
                "actual_model": model,
                "requested_provider": spec.provider,
                "actual_provider": spec.provider,
                "fallback_occurred": False,
                "fallback_reason": None,
                "configuration_version": ANALYSIS_CONFIG_VERSION,
            }

            payload = intelligence_to_response_dict(contract_id, model, intelligence)
            analysis_id = f"ANALYSIS_{uuid.uuid4().hex[:12].upper()}"

            # The Contract summary and immutable AnalysisRun are one write.
            # If the contract was archived while a task was running, the
            # ACTIVE predicate prevents a late result from reviving it.
            query = """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
            CREATE (a:AnalysisRun {
                analysis_id: $analysis_id,
                contract_id: $contract_id,
                tenant_id: $tenant_id,
                status: $intelligence_status,
                model_used: $model_used,
                requested_model: $requested_model,
                actual_model: $actual_model,
                requested_provider: $requested_provider,
                actual_provider: $actual_provider,
                fallback_occurred: $fallback_occurred,
                fallback_reason: $fallback_reason,
                configuration_version: $configuration_version,
                execution_path: $execution_path,
                planned_execution: $planned_execution,
                analysis_method: $analysis_method,
                result_payload: $result_payload,
                created_at: datetime()
            })
            CREATE (c)-[:HAS_ANALYSIS]->(a)
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
                c.analysis_execution_path = $execution_path,
                c.analysis_planned_execution = $planned_execution,
                c.analysis_method = $analysis_method,
                c.model_used = $model_used,
                c.analysis_requested_model = $requested_model,
                c.analysis_actual_model = $actual_model,
                c.analysis_requested_provider = $requested_provider,
                c.analysis_actual_provider = $actual_provider,
                c.analysis_fallback_occurred = $fallback_occurred,
                c.analysis_configuration_version = $configuration_version,
                c.latest_analysis_id = $analysis_id,
                c.analysis_task_state = 'SUCCESS',
                c.intelligence_updated = datetime()
            RETURN c.file_id AS contract_id, a.analysis_id AS analysis_id
            """

            result = self.repository.graph.query(query, {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                "analysis_id": analysis_id,
                "result_payload": field_encryptor.encrypt(json.dumps(payload, default=str)),
                **intelligence_data
            })
            if not result:
                raise RuntimeError("Contract is missing or no longer active; analysis was not persisted")
            
            # Store performance metrics
            self._store_performance_metrics(contract_id, tenant_id, intelligence)
            
            logger.info(f"Stored intelligence result {analysis_id} for contract: {contract_id}")
            
        except Exception as e:
            logger.error(f"Failed to store intelligence results for {contract_id}: {e}")
            raise
    
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

    def _mark_pending_review(self, contract_id: str, tenant_id: str, model: str, risk_level: Optional[str], overall_risk_score: Optional[float]) -> None:
        """Persist the paused state so it survives past the Celery task
        result's own TTL - GET .../status and .../reviews/pending both read
        this directly off the Contract node, same pattern as the normal
        completed-analysis fields _store_intelligence_results sets."""
        self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
            SET c.intelligence_status = 'pending_human_review',
                c.risk_score = $risk_score,
                c.risk_level = $risk_level,
                c.analysis_requested_model = $model,
                c.review_requested_at = datetime(),
                c.analysis_task_state = 'SUCCESS'
            RETURN c.file_id AS contract_id
            """,
            {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                "risk_score": overall_risk_score,
                "risk_level": risk_level,
                "model": model,
            },
        )
        logger.info(f"Contract {contract_id} marked pending_human_review ({risk_level})")

    def list_pending_reviews(self, tenant_id: str) -> list[Dict[str, Any]]:
        rows = self.repository.graph.query(
            """
            MATCH (c:Contract {tenant_id: $tenant_id})
            WHERE c.intelligence_status = 'pending_human_review'
              AND coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
            RETURN c.file_id AS contract_id, c.filename AS filename,
                   c.risk_score AS risk_score, c.risk_level AS risk_level,
                   c.review_requested_at AS requested_at
            ORDER BY c.review_requested_at DESC
            """,
            {"tenant_id": tenant_id},
        )
        return rows or []

    def approve_review(self, contract_id: str, tenant_id: str) -> ContractIntelligence:
        """Resume a paused run (POST .../review/approve) and persist the
        now-complete result exactly like a normal analysis. Reads back the
        model the run was originally started with (_mark_pending_review
        stored it) so cuad_mitigation/redline_generation - which still
        need a real LLM - use the same provider/model the admin's org
        actually configured, not a silent default."""
        rows = self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            WHERE c.intelligence_status = 'pending_human_review'
            RETURN coalesce(c.analysis_requested_model, 'gemini-2.5-flash') AS model
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )
        if not rows:
            raise ValueError(f"Contract {contract_id} has no pending review")
        model = rows[0]["model"]

        start_time = time.time()
        llm = self._get_llm_for_model(model)
        orchestrator = ContractIntelligenceAgentFactory.create_orchestrator(llm)
        analysis_result = orchestrator.resume_analysis(contract_id)

        intelligence = self._convert_to_domain_entities(analysis_result)
        intelligence.processing_time = time.time() - start_time
        spec = self.llm_manager.get_model_spec(model) if self.llm_manager else model_spec(model)
        intelligence.requested_model = model
        intelligence.actual_model = model
        intelligence.requested_provider = spec.provider
        intelligence.actual_provider = spec.provider
        intelligence.fallback_occurred = False
        intelligence.fallback_reason = None
        intelligence.configuration_version = ANALYSIS_CONFIG_VERSION

        self._store_intelligence_results(contract_id, tenant_id, model, intelligence)
        logger.info(f"Contract {contract_id}: review approved, analysis resumed and completed")
        return intelligence

    def reject_review(self, contract_id: str, tenant_id: str) -> None:
        """Terminate a paused run without resuming it. Leaves the Contract
        node honestly in a terminal, non-"completed" state and discards
        the orphaned Redis checkpoint - no code path can ever resume this
        thread_id again after this."""
        from backend.agents.contract_intelligence_agents import _get_redis_checkpointer

        rows = self.repository.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            WHERE c.intelligence_status = 'pending_human_review'
            SET c.intelligence_status = 'review_rejected',
                c.review_resolved_at = datetime()
            RETURN c.file_id AS contract_id
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )
        if not rows:
            raise ValueError(f"Contract {contract_id} has no pending review")
        try:
            _get_redis_checkpointer().delete_thread(contract_id)
        except Exception as e:
            logger.warning(f"Failed to delete checkpoint thread for rejected review {contract_id}: {e}")
        logger.info(f"Contract {contract_id}: review rejected")

class ContractIntelligenceServiceFactory:
    """Factory for creating contract intelligence service"""
    
    @staticmethod
    def create_service(llm_manager: LLMManager) -> ContractIntelligenceService:
        """Create a new contract intelligence service"""
        return ContractIntelligenceService(llm_manager)
