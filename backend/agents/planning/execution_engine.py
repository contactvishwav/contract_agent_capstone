from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import asyncio
import logging
from datetime import datetime
import time
from functools import wraps
from backend.agents.planning.planning_agent import ExecutionPlan, ExecutionStep, StepType
from backend.agents.intelligence_tools import (
    ClauseDetectorTool, PolicyCheckerTool, 
    RiskCalculatorTool, RedlineGeneratorTool
)
from backend.agents.agent_workflow_tracker import workflow_tracker
from backend.agents.supervisor.progress_publisher import publish_step_progress
from backend.agents.supervisor.quality_grader import grade_analysis
from backend.shared.reliability.circuit_breaker import GEMINI_CIRCUIT_BREAKER
from google.api_core.exceptions import ResourceExhausted
import json

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


def _is_quota_exhausted(e: Exception) -> bool:
    """
    True for google.api_core.exceptions.ResourceExhausted directly (kept for
    any client that does raise it), and also for the real exception this
    app's actual Gemini client raises: langchain_google_genai wraps a real
    429 RESOURCE_EXHAUSTED into its own ChatGoogleGenerativeAIError (see
    langchain_google_genai.chat_models._handle_client_error), which is not a
    ResourceExhausted subclass - so `except ResourceExhausted` alone never
    actually matches it, and the "fail fast, don't retry into a 429" logic
    below it silently never fired for a real quota exhaustion. Confirmed via
    live end-to-end testing against the real API (not a mock).
    """
    if isinstance(e, ResourceExhausted):
        return True
    message = str(e)
    return "RESOURCE_EXHAUSTED" in message or "429" in message

@dataclass
class ExecutionResult:
    step_id: str
    success: bool
    output_data: Any
    execution_time_ms: int
    confidence_score: float
    error_message: Optional[str] = None

class StepExecutor:
    """Execute individual analysis steps"""

    def __init__(self, llm=None):
        # Real, confirmed bug found live: this used to construct
        # ClauseDetectorTool()/PolicyCheckerTool() with no llm at all, on
        # the planning path - the actual production default
        # (use_planning=True in the /analyze route). Both tools fall back
        # to the Gemini->OpenAI->Anthropic chain when constructed without
        # an explicit llm, so the user's real "AI Model" selection never
        # reached the model that actually ran, on this path either.
        # RiskCalculatorTool/RedlineGeneratorTool are deterministic - no
        # LLM call, nothing to thread through.
        self.tools = {
            StepType.EXTRACT_CLAUSES: ClauseDetectorTool(llm),
            StepType.CHECK_POLICIES: PolicyCheckerTool(llm),
            StepType.ASSESS_RISK: RiskCalculatorTool(),
            StepType.GENERATE_REDLINES: RedlineGeneratorTool()
        }
    
    async def execute_step(self, step: ExecutionStep, context: Dict[str, Any]) -> ExecutionResult:
        """Execute a single analysis step with timeout and retry"""
        start_time = datetime.now()
        
        # Implement timeout
        try:
            return await asyncio.wait_for(
                self._execute_step_with_retry(step, context),
                timeout=step.timeout_seconds
            )
        except asyncio.TimeoutError:
            return ExecutionResult(
                step_id=step.step_id,
                success=False,
                output_data=None,
                execution_time_ms=step.timeout_seconds * 1000,
                confidence_score=0.0,
                error_message=f"Step timed out after {step.timeout_seconds} seconds"
            )
    
    async def _execute_step_with_retry(self, step: ExecutionStep, context: Dict[str, Any]) -> ExecutionResult:
        """Execute step with retry mechanism"""
        logger.info(f"🔧 STEP EXEC 1: Starting step {step.step_id} with retry mechanism")
        max_retries = 2
        start_time = datetime.now()
        
        # Track step execution
        execution = workflow_tracker.start_agent(
            f"Planned {step.step_type.value.replace('_', ' ').title()} Step",
            step.description,
            self._get_input_summary(step, context)
        )
        
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"Retrying step {step.step_id}, attempt {attempt + 1}")
                    await asyncio.sleep(attempt * 0.5)  # Exponential backoff
                
                logger.info(f"🔧 STEP EXEC 2: Executing {step.step_type} for {step.step_id}")
                
                if step.step_type == StepType.EXTRACT_CLAUSES:
                    result = await self._execute_clause_extraction(step, context)
                elif step.step_type == StepType.CHECK_POLICIES:
                    result = await self._execute_policy_check(step, context)
                elif step.step_type == StepType.ASSESS_RISK:
                    result = await self._execute_risk_assessment(step, context)
                elif step.step_type == StepType.GENERATE_REDLINES:
                    result = await self._execute_redline_generation(step, context)
                elif step.step_type == StepType.VALIDATE_RESULTS:
                    result = await self._execute_validation(step, context)
                elif step.step_type == StepType.CUAD_MITIGATION:
                    result = await self._execute_cuad_mitigation(step, context)
                else:
                    raise ValueError(f"Unknown step type: {step.step_type}")
                
                logger.info(f"🔧 STEP EXEC 3: Step {step.step_id} execution completed successfully")
                
                execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
                workflow_tracker.complete_agent(execution, self._get_output_summary(result))
                
                return ExecutionResult(
                    step_id=step.step_id,
                    success=True,
                    output_data=result,
                    execution_time_ms=execution_time,
                    confidence_score=0.9
                )
                
            except ResourceExhausted as e:
                # Rate limit / quota exceeded - each attempt re-sends the
                # full contract text (or full clause set) to the LLM, so
                # blindly retrying into a fresh 429 up to max_retries times
                # would silently triple the cost of a single failure with
                # no realistic chance of success (a per-day quota wall in
                # particular cannot be waited out within one request's
                # lifetime). Fail fast instead of retrying.
                execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
                workflow_tracker.error_agent(execution, f"Rate limit/quota exceeded: {e}")
                return ExecutionResult(
                    step_id=step.step_id,
                    success=False,
                    output_data=None,
                    execution_time_ms=execution_time,
                    confidence_score=0.0,
                    error_message=f"Rate limit or quota exceeded - not retrying: {e}"
                )
            except Exception as e:
                if _is_quota_exhausted(e):
                    # Same real-world case as the ResourceExhausted branch
                    # above, just raised as a different exception type by
                    # this app's actual Gemini client - see _is_quota_
                    # exhausted's docstring. Fail fast here too.
                    execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
                    workflow_tracker.error_agent(execution, f"Rate limit/quota exceeded: {e}")
                    return ExecutionResult(
                        step_id=step.step_id,
                        success=False,
                        output_data=None,
                        execution_time_ms=execution_time,
                        confidence_score=0.0,
                        error_message=f"Rate limit or quota exceeded - not retrying: {e}"
                    )
                if attempt == max_retries:  # Last attempt failed
                    execution_time = int((datetime.now() - start_time).total_seconds() * 1000)
                    workflow_tracker.error_agent(execution, str(e))

                    return ExecutionResult(
                        step_id=step.step_id,
                        success=False,
                        output_data=None,
                        execution_time_ms=execution_time,
                        confidence_score=0.0,
                        error_message=f"Failed after {max_retries + 1} attempts: {str(e)}"
                    )
                else:
                    logger.warning(f"Step {step.step_id} attempt {attempt + 1} failed: {e}")
                    continue  # Retry
    
    async def _execute_clause_extraction(self, step: ExecutionStep, context: Dict[str, Any]) -> List[Dict]:
        """Execute clause extraction with enhanced planning context"""
        contract_text = context.get("contract_text", "")
        tool = self.tools[StepType.EXTRACT_CLAUSES]
        result_json = tool._run(contract_text, contract_id=context.get("contract_id"), tenant_id=context.get("tenant_id"))
        return json.loads(result_json)

    async def _execute_policy_check(self, step: ExecutionStep, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute policy checking with dependency results. Returns
        PolicyCheckerTool's {"violations": [...], "failed_clause_ids": [...],
        "status": ...} dict as-is - see _update_context_with_result and
        _compute_step_status for how the pieces get unpacked."""
        clauses = context.get("extracted_clauses", [])
        tool = self.tools[StepType.CHECK_POLICIES]
        result_json = tool._run(
            json.dumps(clauses),
            contract_id=context.get("contract_id"),
            tenant_id=context.get("tenant_id"),
            contract_type=context.get("contract_type") or "general",
        )
        return json.loads(result_json)

    async def _execute_risk_assessment(self, step: ExecutionStep, context: Dict[str, Any]) -> Dict:
        """Execute risk assessment with enhanced analysis"""
        clauses = context.get("extracted_clauses", [])
        violations = context.get("policy_violations", [])
        tool = self.tools[StepType.ASSESS_RISK]
        result_json = tool._run(json.dumps(clauses), json.dumps(violations), contract_id=context.get("contract_id"), tenant_id=context.get("tenant_id"))
        return json.loads(result_json)

    async def _execute_redline_generation(self, step: ExecutionStep, context: Dict[str, Any]) -> List[Dict]:
        """Execute redline generation with comprehensive context"""
        violations = context.get("policy_violations", [])
        tool = self.tools[StepType.GENERATE_REDLINES]
        result_json = tool._run(json.dumps(violations), contract_id=context.get("contract_id"), tenant_id=context.get("tenant_id"))
        return json.loads(result_json)
    
    async def _execute_validation(self, step: ExecutionStep, context: Dict[str, Any]) -> Dict:
        """Execute cross-validation of results"""
        # Validate consistency between risk assessment and policy violations
        risk_data = context.get("risk_data", {})
        violations = context.get("policy_violations", [])
        
        validation_score = 1.0
        issues = []
        
        # Check if high-risk score aligns with critical violations
        risk_score = risk_data.get("overall_risk_score", 0)
        critical_violations = len([v for v in violations if v.get("severity") == "CRITICAL"])
        
        if risk_score > 80 and critical_violations == 0:
            validation_score -= 0.3
            issues.append("High risk score without critical violations")
        
        if risk_score < 40 and critical_violations > 2:
            validation_score -= 0.3
            issues.append("Low risk score with multiple critical violations")
        
        return {
            "validation_score": max(0.0, validation_score),
            "issues": issues,
            "validated_at": datetime.now().isoformat()
        }
    
    async def _execute_cuad_mitigation(self, step: ExecutionStep, context: Dict[str, Any]) -> Dict:
        """Execute enhanced CUAD mitigation analysis"""
        try:
            # Try optimized Phase 3 tools first
            from backend.agents.optimized_cuad_tools import (
                OptimizedDeviationDetectorTool, OptimizedJurisdictionAdapterTool, OptimizedPrecedentMatcherTool
            )
            from backend.agents.feedback_learning_system import AdaptiveAnalyzer, compute_baseline_risk_level

            clauses = context.get("extracted_clauses", [])
            contract_text = context.get("contract_text", "")
            violations = context.get("policy_violations", [])
            tenant_id = context.get("tenant_id")
            if not tenant_id:
                raise ValueError("Authenticated tenant_id is required for CUAD precedent matching")

            # Run optimized CUAD tools
            deviation_tool = OptimizedDeviationDetectorTool()
            jurisdiction_tool = OptimizedJurisdictionAdapterTool()
            precedent_tool = OptimizedPrecedentMatcherTool()

            deviations = json.loads(deviation_tool._run(json.dumps(clauses), tenant_id))
            jurisdiction_info = json.loads(jurisdiction_tool._run(contract_text))
            precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses), tenant_id))

            # Apply adaptive learning on top of a real, computed baseline
            # risk_level - CHECK_POLICIES already ran (it's a dependency of
            # this step in both plan templates), so per-clause violation
            # severity is real data here, not a guess.
            adaptive_analyzer = AdaptiveAnalyzer()
            enhanced_clauses = []
            for clause in clauses:
                baseline_clause = {**clause, "risk_level": compute_baseline_risk_level(clause, violations)}
                enhanced_analysis = adaptive_analyzer.enhance_analysis(baseline_clause, baseline_clause)
                enhanced_clauses.append(enhanced_analysis)
            
            return {
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "enhanced_clauses": enhanced_clauses,
                "analysis_method": "optimized_phase3"
            }
            
        except Exception as e:
            logger.warning(f"Optimized CUAD tools failed, falling back to enhanced tools: {e}")
            
            # Try Phase 2 tools
            try:
                from backend.agents.enhanced_cuad_tools import (
                    EnhancedDeviationDetectorTool, EnhancedJurisdictionAdapterTool, EnhancedPrecedentMatcherTool
                )
                
                clauses = context.get("extracted_clauses", [])
                contract_text = context.get("contract_text", "")
                
                deviation_tool = EnhancedDeviationDetectorTool()
                jurisdiction_tool = EnhancedJurisdictionAdapterTool()
                precedent_tool = EnhancedPrecedentMatcherTool()
                
                deviations = json.loads(deviation_tool._run(json.dumps(clauses)))
                jurisdiction_info = json.loads(jurisdiction_tool._run(contract_text))
                precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses), tenant_id))
                
                return {
                    "cuad_deviations": deviations,
                    "jurisdiction_info": jurisdiction_info,
                    "precedent_matches": precedent_matches,
                    "analysis_method": "enhanced_phase2_fallback"
                }
                
            except Exception as e2:
                logger.warning(f"Enhanced CUAD tools also failed, falling back to Phase 1: {e2}")
            
            # Fallback to Phase 1 tools
            from backend.agents.cuad_mitigation_tools import (
                DeviationDetectorTool, JurisdictionAdapterTool, PrecedentMatcherTool
            )
            
            clauses = context.get("extracted_clauses", [])
            contract_text = context.get("contract_text", "")
            
            deviation_tool = DeviationDetectorTool()
            jurisdiction_tool = JurisdictionAdapterTool()
            precedent_tool = PrecedentMatcherTool()
            
            deviations = json.loads(deviation_tool._run(json.dumps(clauses)))
            jurisdiction_info = json.loads(jurisdiction_tool._run(contract_text))
            precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses)))
            
            return {
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "analysis_method": "fallback_phase1"
            }
    
    def _get_input_summary(self, step: ExecutionStep, context: Dict[str, Any]) -> str:
        """Get human-readable input summary for tracking"""
        if step.step_type == StepType.EXTRACT_CLAUSES:
            text_len = len(context.get("contract_text", ""))
            return f"Contract text ({text_len:,} characters)"
        elif step.step_type == StepType.CHECK_POLICIES:
            clause_count = len(context.get("extracted_clauses", []))
            return f"{clause_count} extracted clauses"
        elif step.step_type == StepType.ASSESS_RISK:
            clauses = len(context.get("extracted_clauses", []))
            violations = len(context.get("policy_violations", []))
            return f"{clauses} clauses + {violations} violations"
        elif step.step_type == StepType.GENERATE_REDLINES:
            violation_count = len(context.get("policy_violations", []))
            return f"{violation_count} policy violations"
        elif step.step_type == StepType.VALIDATE_RESULTS:
            return "Cross-validation of analysis results"
        return "Analysis context"
    
    def _get_output_summary(self, result: Any) -> str:
        """Get human-readable output summary for tracking"""
        if isinstance(result, list):
            return f"Generated {len(result)} items"
        elif isinstance(result, dict):
            if "overall_risk_score" in result:
                score = result["overall_risk_score"]
                level = result.get("risk_level", "UNKNOWN")
                return f"Risk Score: {score}/100 ({level})"
            elif "validation_score" in result:
                score = result["validation_score"]
                return f"Validation Score: {score:.2f}"
            else:
                return f"Analysis result with {len(result)} fields"
        return "Analysis completed"

class PlanExecutionEngine:
    """Execute planned analysis workflows with dependency management"""
    
    def __init__(self, llm=None):
        self.step_executor = StepExecutor(llm)
        self.execution_context: Dict[str, Any] = {}
    
    async def execute_plan(self, plan: ExecutionPlan, contract_text: str, contract_id: Optional[str] = None, tenant_id: Optional[str] = None, contract_type: Optional[str] = None) -> Dict[str, Any]:
        """Execute the complete analysis plan"""
        logger.info(f"🚀 EXEC STEP 1: Starting plan execution {plan.plan_id} with {len(plan.steps)} steps")
        logger.info(f"🚀 EXEC STEP 2: Contract text length: {len(contract_text)} characters")

        # Initialize execution context
        self.execution_context = {
            "contract_text": contract_text,
            "contract_id": contract_id,
            "tenant_id": tenant_id,
            "contract_type": contract_type,
            "plan_id": plan.plan_id,
            "execution_start": datetime.now()
        }
        
        # Don't call workflow_tracker.start_workflow() here - the caller
        # (contract_intelligence_agents.py's _analyze_with_planning) now
        # genuinely does this before invoking execute_plan (a real bug,
        # found live, until it did - see that method's own comment).
        # complete_workflow() below assumes start_workflow() already ran.

        publish_step_progress(contract_id, tenant_id, "workflow", "started", plan_id=plan.plan_id, step_count=len(plan.steps))

        step_results: Dict[str, ExecutionResult] = {}
        step_status: Dict[str, str] = {}

        try:
            # Execute steps respecting dependencies
            logger.info(f"🚀 EXEC STEP 3: Starting step execution loop")
            for i, step in enumerate(plan.steps):
                logger.info(f"🚀 EXEC STEP 4.{i+1}: Processing step {step.step_id} ({step.step_type})")

                # Wait for dependencies
                await self._wait_for_dependencies(step, step_results)
                logger.info(f"🚀 EXEC STEP 4.{i+1}a: Dependencies satisfied for {step.step_id}")

                # Execute step
                logger.info(f"🚀 EXEC STEP 4.{i+1}b: Executing step {step.step_id}")
                result = await self.step_executor.execute_step(step, self.execution_context)
                step_results[step.step_id] = result
                status = self._compute_step_status(result, step.step_type)
                step_status[step.step_type.value] = status
                publish_step_progress(contract_id, tenant_id, step.step_type.value, status, step_id=step.step_id)
                logger.info(f"🚀 EXEC STEP 4.{i+1}c: Step {step.step_id} completed, success: {result.success}")

                # Update context with results
                if result.success:
                    self._update_context_with_result(step, result)
                    logger.info(f"🚀 EXEC STEP 4.{i+1}d: Context updated for {step.step_id}")
                else:
                    logger.error(f"🚀 EXEC ERROR: Step {step.step_id} failed: {result.error_message}")
                    # Continue execution for non-critical failures

            # Complete workflow tracking
            workflow_tracker.complete_workflow()

            # Return final results in expected format
            result = self._format_final_results(step_status)
            self._log_escalation_if_needed(result, contract_id, tenant_id)
            publish_step_progress(contract_id, tenant_id, "workflow", "complete", grade=result.get("quality_grade", {}).get("grade"))
            return result

        except Exception as e:
            logger.error(f"Plan execution failed: {e}")
            workflow_tracker.complete_workflow()
            result = self._format_error_results(str(e), step_status)
            self._log_escalation_if_needed(result, contract_id, tenant_id)
            publish_step_progress(
                contract_id,
                tenant_id,
                "workflow",
                "failed",
                error_type=type(e).__name__,
            )
            return result

    def _log_escalation_if_needed(self, result: Dict[str, Any], contract_id: Optional[str], tenant_id: Optional[str]) -> None:
        """Writes one roll-up WORKFLOW_ESCALATION audit event when
        result["escalated"] is True (see _format_final_results/_format_
        error_results for how that's computed) - queryable via the
        existing GET /api/audit/trail/{contract_id} route. Never raises: a
        logging failure must not turn an already-computed real analysis
        result into a hard error."""
        if not result.get("escalated"):
            return
        try:
            from backend.infrastructure.audit_logger import AuditLogger, AuditEventType
            AuditLogger().log_event(
                event_type=AuditEventType.WORKFLOW_ESCALATION,
                resource_id=contract_id or "unknown",
                tenant_id=tenant_id,
                action="workflow_escalation",
                metadata={
                    "node_status": result.get("node_status", {}),
                    "quality_grade": result.get("quality_grade", {}).get("grade"),
                },
                status="failure",
            )
        except Exception as e:
            logger.warning(f"Failed to log workflow escalation audit event: {e}")
    
    async def _wait_for_dependencies(self, step: ExecutionStep, step_results: Dict[str, ExecutionResult]):
        """Wait for step dependencies to complete"""
        for dep_id in step.dependencies:
            while dep_id not in step_results:
                await asyncio.sleep(0.1)  # Wait for dependency
            
            if not step_results[dep_id].success:
                logger.warning(f"Dependency {dep_id} failed for step {step.step_id}")
    
    def _compute_step_status(self, result: ExecutionResult, step_type: Optional[StepType] = None) -> str:
        """
        "success" / "failed" as before, plus a new "partial" state for a
        step that completed (no exception, ExecutionResult.success=True) but
        whose own output reports it didn't fully succeed - currently just
        PolicyCheckerTool's per-clause partial-failure status
        (backend/agents/intelligence_tools.py's PolicyCheckerTool._run),
        threaded through as output_data["status"] when output_data is a
        dict. Anything else keeps the original binary success/failed.

        EXTRACT_CLAUSES special case (a real gap found live): ClauseDetector
        Tool._run catches every exception from LLMExtractionService.extract_
        clauses - including CircuitBreakerOpenError - and returns an empty
        list, which this method would otherwise report as an ordinary
        "success" with zero clauses. That's indistinguishable from "this
        contract genuinely has no CUAD clauses" (vanishingly rare for a real
        contract) from "the Gemini circuit breaker was open and nothing was
        actually attempted." Checking the real, already-built circuit
        breaker's live state here - only when the step's own result came
        back empty, so a real successful extraction is never second-guessed
        - surfaces that distinction using a signal that already exists,
        rather than reshaping ClauseDetectorTool's return type (which
        `extracted_clauses` is consumed as a bare list in several other
        places for).
        """
        if not result.success:
            return "failed"
        if isinstance(result.output_data, dict):
            tool_status = result.output_data.get("status")
            if tool_status == "partial":
                return "partial"
            if tool_status == "failure":
                return "failed"
        if (
            step_type == StepType.EXTRACT_CLAUSES
            and not result.output_data
            and GEMINI_CIRCUIT_BREAKER.get_status()["state"] != "closed"
        ):
            return "failed"
        return "success"

    def _update_context_with_result(self, step: ExecutionStep, result: ExecutionResult):
        """Update execution context with step results"""
        if step.step_type == StepType.EXTRACT_CLAUSES:
            self.execution_context["extracted_clauses"] = result.output_data
        elif step.step_type == StepType.CHECK_POLICIES:
            output = result.output_data
            if isinstance(output, dict) and "violations" in output:
                self.execution_context["policy_violations"] = output["violations"]
                self.execution_context["policy_check_failed_clause_ids"] = output.get("failed_clause_ids", [])
            else:
                self.execution_context["policy_violations"] = output
        elif step.step_type == StepType.ASSESS_RISK:
            self.execution_context["risk_data"] = result.output_data
        elif step.step_type == StepType.GENERATE_REDLINES:
            self.execution_context["redline_suggestions"] = result.output_data
        elif step.step_type == StepType.VALIDATE_RESULTS:
            self.execution_context["validation_results"] = result.output_data
        elif step.step_type == StepType.CUAD_MITIGATION:
            cuad_data = result.output_data
            self.execution_context["cuad_deviations"] = cuad_data.get("cuad_deviations", [])
            self.execution_context["jurisdiction_info"] = cuad_data.get("jurisdiction_info", {})
            self.execution_context["precedent_matches"] = cuad_data.get("precedent_matches", [])
            # Real "Degrade" recovery, already running (_execute_cuad_
            # mitigation's Phase3 -> Phase2 -> Phase1 fallback cascade) but
            # previously discarded here immediately after being computed -
            # analysis_method never reached execution_context or the API
            # response, so a degraded-but-successful analysis looked
            # identical to a full Phase-3 one. Surfaced now via
            # _format_final_results's "analysis_method" field.
            self.execution_context["cuad_analysis_method"] = cuad_data.get("analysis_method")
            # Previously discarded entirely - AdaptiveAnalyzer's output
            # (baseline + any learned-pattern risk adjustment per clause)
            # never reached execution_context or the response. Falls back
            # to the pre-CUAD_MITIGATION clauses if absent (e.g. a fallback/
            # error path in _execute_cuad_mitigation that doesn't produce
            # enhanced_clauses at all), matching the other three keys'
            # `.get(..., <fallback>)` pattern above.
            enhanced_clauses = cuad_data.get("enhanced_clauses")
            if enhanced_clauses is not None:
                self.execution_context["extracted_clauses"] = enhanced_clauses
    
    def _format_final_results(self, step_status: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Format results in the expected contract intelligence format"""
        step_status = step_status or {}
        # "partial" (some, but not all, of a step's inputs failed - e.g.
        # policy_checking with some clauses that couldn't be evaluated)
        # must also block a dishonest "processing_complete: True", not just
        # a hard "failed".
        any_failed = any(s in ("failed", "partial") for s in step_status.values())
        # "Escalate" recovery signal: any step that genuinely failed
        # outright (not just "partial") marks the whole workflow for human
        # review - broader/more sensitive than the quality grade, which
        # can be F purely from a low grounding rate with no step literally
        # failing. execute_plan writes the actual WORKFLOW_ESCALATION
        # audit event; this flag is what it's computed from.
        escalated = any(s == "failed" for s in step_status.values())
        result = {
            "clauses": self.execution_context.get("extracted_clauses", []),
            "violations": self.execution_context.get("policy_violations", []),
            "risk_assessment": self.execution_context.get("risk_data", {}),
            "redlines": self.execution_context.get("redline_suggestions", []),
            "cuad_deviations": self.execution_context.get("cuad_deviations", []),
            "jurisdiction_info": self.execution_context.get("jurisdiction_info", {}),
            "precedent_matches": self.execution_context.get("precedent_matches", []),
            "validation": self.execution_context.get("validation_results", {}),
            "analysis_method": self.execution_context.get("cuad_analysis_method"),
            "node_status": step_status,
            "processing_complete": not any_failed,
            "escalated": escalated,
            "planned_execution": True,
            "execution_path": "plan_execution_engine",
        }
        # Built on signals already in `result` above - see quality_grader.py's
        # module docstring for the full rationale. Additive: nothing above
        # this line changes shape.
        result["quality_grade"] = grade_analysis(result)
        return result

    def _format_error_results(self, error_message: str, step_status: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Format error results"""
        step_status = step_status or {}
        result = {
            "clauses": [],
            "violations": [],
            "risk_assessment": {"overall_risk_score": 0, "risk_level": "UNKNOWN"},
            "redlines": [],
            "analysis_method": None,
            "node_status": step_status,
            "processing_complete": False,
            "planned_execution": True,
            "execution_path": "plan_execution_engine",
            # A hard abort always warrants review, regardless of whether
            # any individual step had already been marked "failed" yet.
            "escalated": True,
            "error": error_message
        }
        result["quality_grade"] = grade_analysis(result)
        return result
