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
import json

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

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
    
    def __init__(self):
        self.tools = {
            StepType.EXTRACT_CLAUSES: ClauseDetectorTool(),
            StepType.CHECK_POLICIES: PolicyCheckerTool(),
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
                
            except Exception as e:
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

    async def _execute_policy_check(self, step: ExecutionStep, context: Dict[str, Any]) -> List[Dict]:
        """Execute policy checking with dependency results"""
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
            from backend.agents.feedback_learning_system import AdaptiveAnalyzer
            
            clauses = context.get("extracted_clauses", [])
            contract_text = context.get("contract_text", "")
            
            # Run optimized CUAD tools
            deviation_tool = OptimizedDeviationDetectorTool()
            jurisdiction_tool = OptimizedJurisdictionAdapterTool()
            precedent_tool = OptimizedPrecedentMatcherTool()
            
            deviations = json.loads(deviation_tool._run(json.dumps(clauses)))
            jurisdiction_info = json.loads(jurisdiction_tool._run(contract_text))
            precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses)))
            
            # Apply adaptive learning
            adaptive_analyzer = AdaptiveAnalyzer()
            enhanced_clauses = []
            for clause in clauses:
                enhanced_analysis = adaptive_analyzer.enhance_analysis(clause, clause)
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
                precedent_matches = json.loads(precedent_tool._run(json.dumps(clauses)))
                
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
    
    def __init__(self):
        self.step_executor = StepExecutor()
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
        
        # Don't reset workflow tracker - planning agent already started it
        # workflow_tracker.start_workflow()
        
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
                step_status[step.step_type.value] = "success" if result.success else "failed"
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
            return self._format_final_results(step_status)

        except Exception as e:
            logger.error(f"Plan execution failed: {e}")
            workflow_tracker.complete_workflow()
            return self._format_error_results(str(e), step_status)
    
    async def _wait_for_dependencies(self, step: ExecutionStep, step_results: Dict[str, ExecutionResult]):
        """Wait for step dependencies to complete"""
        for dep_id in step.dependencies:
            while dep_id not in step_results:
                await asyncio.sleep(0.1)  # Wait for dependency
            
            if not step_results[dep_id].success:
                logger.warning(f"Dependency {dep_id} failed for step {step.step_id}")
    
    def _update_context_with_result(self, step: ExecutionStep, result: ExecutionResult):
        """Update execution context with step results"""
        if step.step_type == StepType.EXTRACT_CLAUSES:
            self.execution_context["extracted_clauses"] = result.output_data
        elif step.step_type == StepType.CHECK_POLICIES:
            self.execution_context["policy_violations"] = result.output_data
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
    
    def _format_final_results(self, step_status: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Format results in the expected contract intelligence format"""
        step_status = step_status or {}
        any_failed = any(s == "failed" for s in step_status.values())
        return {
            "clauses": self.execution_context.get("extracted_clauses", []),
            "violations": self.execution_context.get("policy_violations", []),
            "risk_assessment": self.execution_context.get("risk_data", {}),
            "redlines": self.execution_context.get("redline_suggestions", []),
            "cuad_deviations": self.execution_context.get("cuad_deviations", []),
            "jurisdiction_info": self.execution_context.get("jurisdiction_info", {}),
            "precedent_matches": self.execution_context.get("precedent_matches", []),
            "validation": self.execution_context.get("validation_results", {}),
            "node_status": step_status,
            "processing_complete": not any_failed,
            "planned_execution": True
        }

    def _format_error_results(self, error_message: str, step_status: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Format error results"""
        return {
            "clauses": [],
            "violations": [],
            "risk_assessment": {"overall_risk_score": 0, "risk_level": "UNKNOWN"},
            "redlines": [],
            "node_status": step_status or {},
            "processing_complete": False,
            "error": error_message
        }