from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage
from backend.agents.intelligence_state import IntelligenceState
from backend.agents.intelligence_tools import (
    ClauseDetectorTool, PolicyCheckerTool, 
    RiskCalculatorTool, RedlineGeneratorTool
)
from backend.agents.agent_workflow_tracker import workflow_tracker
from backend.agents.planning.planning_agent import PlanningAgentFactory
from backend.agents.planning.execution_engine import PlanExecutionEngine
import json
import logging
from typing import Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class IntelligenceOrchestrator:
    """Proper multi-agent orchestrator following SOLID principles"""
    
    def __init__(self, llm):
        self.llm = llm
        self.workflow = self._build_workflow()
        self.planning_agent = PlanningAgentFactory.create_planning_agent()
        # Real, confirmed bug found live: self.llm (the model the user
        # actually selected, resolved from the "AI Model" dropdown all the
        # way up in contract_intelligence_service.py's _get_llm_for_model)
        # used to be resolved here and then never read again - every real
        # analysis used ClauseDetectorTool()/PolicyCheckerTool() with no
        # llm argument, which (per the LLM fallback build) falls back to
        # the Gemini->OpenAI->Anthropic chain regardless of what the
        # dropdown said. Threaded through to both real orchestration paths
        # below (this one and the planning/PlanExecutionEngine one, which
        # is the actual production default - use_planning=True).
        self.execution_engine = PlanExecutionEngine(llm)
    
    def _build_workflow(self) -> StateGraph:
        """Build workflow with proper state management"""
        
        workflow = StateGraph(IntelligenceState)
        
        # Add nodes with descriptive names (no conflicts)
        workflow.add_node("clause_extraction", self._extract_clauses)
        workflow.add_node("policy_checking", self._check_policies)
        workflow.add_node("risk_calculation", self._calculate_risks)

        # NEW: CUAD mitigation step (Phase 1)
        workflow.add_node("cuad_mitigation", self._cuad_mitigation)

        workflow.add_node("redline_generation", self._generate_redlines)

        # Define workflow with CUAD steps
        workflow.set_entry_point("clause_extraction")
        workflow.add_edge("clause_extraction", "policy_checking")
        workflow.add_edge("policy_checking", "risk_calculation")
        workflow.add_edge("risk_calculation", "cuad_mitigation")
        workflow.add_edge("cuad_mitigation", "redline_generation")
        workflow.add_edge("redline_generation", END)
        
        return workflow.compile()
    
    def _extract_clauses(self, state: IntelligenceState) -> IntelligenceState:
        """Extract clauses - Single Responsibility"""
        text_len = len(state["contract_text"])
        execution = workflow_tracker.start_agent(
            "Clause Extraction Agent",
            "Extract key contract clauses (Payment, Liability, IP, etc.)",
            f"Contract text ({text_len:,} characters)"
        )

        try:
            tool = ClauseDetectorTool(self.llm)
            clauses_json = tool._run(
                state["contract_text"],
                contract_id=state.get("contract_id"),
                tenant_id=state.get("tenant_id"),
            )
            clauses_list = json.loads(clauses_json)

            workflow_tracker.complete_agent(execution, f"Extracted {len(clauses_list)} clauses")

            return {**state,
                "extracted_clauses": clauses_list,
                "current_step": "clause_extraction",
                "node_status": {**state.get("node_status", {}), "clause_extraction": "success"},
            }
        except Exception as e:
            workflow_tracker.error_agent(execution, f"Clause extraction failed: {e}")
            return {**state,
                "extracted_clauses": [],
                "processing_result": {"status": "error", "error": f"Clause extraction failed: {e}"},
                "node_status": {**state.get("node_status", {}), "clause_extraction": "error"},
            }
    
    def _check_policies(self, state: IntelligenceState) -> IntelligenceState:
        """Check policy compliance - Single Responsibility"""
        clause_count = len(state["extracted_clauses"])
        execution = workflow_tracker.start_agent(
            "Policy Compliance Agent",
            "Check clauses against company policies (Payment, Liability, IP, etc.)",
            f"{clause_count} extracted clauses"
        )
        
        try:
            tool = PolicyCheckerTool(self.llm)
            clauses_json = json.dumps(state["extracted_clauses"])
            result_json = tool._run(
                clauses_json,
                contract_id=state.get("contract_id"),
                tenant_id=state.get("tenant_id"),
                contract_type=state.get("contract_type") or "general",
            )
            result = json.loads(result_json)
            violations_list = result.get("violations", [])
            # PolicyCheckerTool's own status: "success" (every clause
            # evaluated cleanly), "partial" (some clauses failed to
            # evaluate - quota/network/parsing errors - but others
            # succeeded), or "failure" (every clause failed). Map "failure"
            # onto this node's existing "error" vocabulary since the data is
            # just as unreliable as a hard crash; "partial" is a new,
            # honest middle state - some real violations may still be in
            # violations_list, but it is not a complete picture.
            tool_status = result.get("status", "success")
            node_status_value = {"success": "success", "partial": "partial", "failure": "error"}.get(tool_status, "success")

            critical_count = len([v for v in violations_list if v.get("severity") == "CRITICAL"])
            summary = f"Found {len(violations_list)} violations ({critical_count} critical)"
            if tool_status != "success":
                summary += f" - {len(result.get('failed_clause_ids', []))} clauses failed evaluation"
            workflow_tracker.complete_agent(execution, summary)

            return {**state,
                "policy_violations": violations_list,
                "current_step": "policy_checking",
                "node_status": {**state.get("node_status", {}), "policy_checking": node_status_value},
            }
        except Exception as e:
            workflow_tracker.error_agent(execution, f"Policy checking failed: {e}")
            return {**state, "policy_violations": [],
                "node_status": {**state.get("node_status", {}), "policy_checking": "error"},
            }
    
    def _calculate_risks(self, state: IntelligenceState) -> IntelligenceState:
        """Calculate risks - Single Responsibility"""
        violation_count = len(state["policy_violations"])
        execution = workflow_tracker.start_agent(
            "Risk Assessment Agent",
            "Calculate overall contract risk score and recommendations",
            f"{len(state['extracted_clauses'])} clauses + {violation_count} violations"
        )
        
        try:
            tool = RiskCalculatorTool()
            clauses_json = json.dumps(state["extracted_clauses"])
            violations_json = json.dumps(state["policy_violations"])
            risk_json = tool._run(
                clauses_json,
                violations_json,
                contract_id=state.get("contract_id"),
                tenant_id=state.get("tenant_id"),
            )
            risk_dict = json.loads(risk_json)

            risk_score = risk_dict.get("overall_risk_score", 0)
            risk_level = risk_dict.get("risk_level", "UNKNOWN")
            workflow_tracker.complete_agent(execution, f"Risk Score: {risk_score}/100 ({risk_level})")

            node_status_value = "error" if risk_level == "ERROR" else "success"
            return {**state,
                "risk_data": risk_dict,
                "current_step": "risk_calculation",
                "node_status": {**state.get("node_status", {}), "risk_calculation": node_status_value},
            }
        except Exception as e:
            workflow_tracker.error_agent(execution, f"Risk calculation failed: {e}")
            return {**state, "risk_data": {"overall_risk_score": None, "risk_level": "ERROR", "error": str(e)},
                "node_status": {**state.get("node_status", {}), "risk_calculation": "error"},
            }
    
    def _generate_redlines(self, state: IntelligenceState) -> IntelligenceState:
        """Generate redlines - Single Responsibility"""
        violation_count = len(state["policy_violations"])
        execution = workflow_tracker.start_agent(
            "Redline Generation Agent",
            "Generate contract redline suggestions for policy violations",
            f"{violation_count} policy violations"
        )
        
        try:
            tool = RedlineGeneratorTool()
            violations_json = json.dumps(state["policy_violations"])
            redlines_json = tool._run(
                violations_json,
                contract_id=state.get("contract_id"),
                tenant_id=state.get("tenant_id"),
            )
            redlines_list = json.loads(redlines_json)

            critical_redlines = len([r for r in redlines_list if r.get("priority") == "CRITICAL"])
            workflow_tracker.complete_agent(execution, f"Generated {len(redlines_list)} redlines ({critical_redlines} critical)")

            return {**state,
                "redline_suggestions": redlines_list,
                "is_complete": True,
                "processing_result": {"status": "success", "message": "Intelligence analysis completed"},
                "node_status": {**state.get("node_status", {}), "redline_generation": "success"},
            }
        except Exception as e:
            workflow_tracker.error_agent(execution, f"Redline generation failed: {e}")
            return {**state, "redline_suggestions": [], "is_complete": False,
                "processing_result": {"status": "error", "error": f"Redline generation failed: {e}"},
                "node_status": {**state.get("node_status", {}), "redline_generation": "error"},
            }
    
    def _cuad_mitigation(self, state: IntelligenceState) -> IntelligenceState:
        """Enhanced CUAD mitigation analysis - Phase 2 implementation"""
        execution = workflow_tracker.start_agent(
            "Enhanced CUAD Mitigation Agent",
            "Advanced deviation detection, jurisdiction adaptation, and precedent analysis with ML",
            f"{len(state['extracted_clauses'])} clauses + {len(state['policy_violations'])} violations"
        )
        
        try:
            # Use optimized tools for Phase 3
            from backend.agents.optimized_cuad_tools import (
                OptimizedDeviationDetectorTool, OptimizedJurisdictionAdapterTool, OptimizedPrecedentMatcherTool
            )
            from backend.agents.feedback_learning_system import AdaptiveAnalyzer, compute_baseline_risk_level

            clauses_json = json.dumps(state["extracted_clauses"])
            
            # 1. Optimized deviation detection with caching and monitoring
            deviation_tool = OptimizedDeviationDetectorTool()
            deviations_json = deviation_tool._run(clauses_json, state["tenant_id"])
            deviations = json.loads(deviations_json)
            
            # 2. Optimized jurisdiction adaptation with caching
            jurisdiction_tool = OptimizedJurisdictionAdapterTool()
            jurisdiction_json = jurisdiction_tool._run(state["contract_text"])
            jurisdiction_info = json.loads(jurisdiction_json)
            
            # 3. Optimized precedent matching with parallel processing
            precedent_tool = OptimizedPrecedentMatcherTool()
            precedents_json = precedent_tool._run(clauses_json, state["tenant_id"])
            precedent_matches = json.loads(precedents_json)
            
            # 4. Apply learned patterns from legal team feedback, on top of
            # a real, computed baseline risk_level (policy_violations is
            # already populated - _check_policies always runs before this
            # node).
            adaptive_analyzer = AdaptiveAnalyzer()
            enhanced_clauses = []
            for clause in state["extracted_clauses"]:
                baseline_clause = {**clause, "risk_level": compute_baseline_risk_level(clause, state["policy_violations"])}
                enhanced_analysis = adaptive_analyzer.enhance_analysis(baseline_clause, baseline_clause)
                enhanced_clauses.append(enhanced_analysis)
            
            # Merge deviations with existing violations
            enhanced_violations = state["policy_violations"] + deviations
            
            # Update risk data with enhanced CUAD insights
            enhanced_risk_data = dict(state["risk_data"])
            if deviations:
                deviation_risk = len([d for d in deviations if d.get("severity") in ["HIGH", "CRITICAL"]])
                enhanced_risk_data["cuad_deviation_risk"] = deviation_risk
                enhanced_risk_data["jurisdiction_compliance"] = jurisdiction_info.get("jurisdiction", "unknown")
                enhanced_risk_data["industry_risk_factors"] = jurisdiction_info.get("risk_factors", [])
                
                # Add precedent-based risk assessment
                if precedent_matches:
                    avg_approval_rate = sum(p.get("approval_rate", 0) for p in precedent_matches) / len(precedent_matches)
                    enhanced_risk_data["precedent_approval_rate"] = avg_approval_rate
            
            # Validate results
            from backend.validation.cuad_validator import validate_cuad_analysis
            
            validation_result = validate_cuad_analysis({
                "clauses": state["extracted_clauses"],
                "cuad_deviations": deviations,
                "risk_assessment": enhanced_risk_data,
                "policy_violations": enhanced_violations
            })
            
            workflow_tracker.complete_agent(
                execution, 
                f"Optimized analysis: {len(deviations)} deviations, jurisdiction: {jurisdiction_info.get('jurisdiction', 'unknown')} ({jurisdiction_info.get('industry', 'general')}), {len(precedent_matches)} precedent matches [validated: {validation_result.is_valid}, confidence: {validation_result.confidence_score:.2f}]"
            )
            
            return {**state,
                "extracted_clauses": enhanced_clauses,
                "policy_violations": enhanced_violations,
                "risk_data": enhanced_risk_data,
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "validation_result": validation_result,
                "current_step": "cuad_mitigation"
            }
            
        except Exception as e:
            workflow_tracker.error_agent(execution, f"Optimized CUAD mitigation failed: {e}")
            # Fallback to Phase 2 tools, then Phase 1
            logger.warning(f"Falling back from Phase 3 tools: {e}")
            return self._cuad_mitigation_fallback_enhanced(state, execution)
    
    def _cuad_mitigation_fallback_enhanced(self, state: IntelligenceState, execution) -> IntelligenceState:
        """Enhanced fallback: Phase 2 -> Phase 1 tools"""
        try:
            # Try Phase 2 tools first
            from backend.agents.enhanced_cuad_tools import (
                EnhancedDeviationDetectorTool, EnhancedJurisdictionAdapterTool, EnhancedPrecedentMatcherTool
            )
            
            clauses_json = json.dumps(state["extracted_clauses"])
            
            deviation_tool = EnhancedDeviationDetectorTool()
            deviations = json.loads(deviation_tool._run(clauses_json))
            
            jurisdiction_tool = EnhancedJurisdictionAdapterTool()
            jurisdiction_info = json.loads(jurisdiction_tool._run(state["contract_text"]))
            
            precedent_tool = EnhancedPrecedentMatcherTool()
            precedent_matches = json.loads(precedent_tool._run(clauses_json, state["tenant_id"]))
            
            enhanced_violations = state["policy_violations"] + deviations
            enhanced_risk_data = dict(state["risk_data"])
            
            workflow_tracker.complete_agent(execution, f"Phase 2 fallback completed: {len(deviations)} deviations")
            
            return {**state,
                "policy_violations": enhanced_violations,
                "risk_data": enhanced_risk_data,
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "current_step": "cuad_mitigation"
            }
            
        except Exception as phase2_error:
            logger.warning(f"Phase 2 fallback failed, trying Phase 1: {phase2_error}")
            return self._cuad_mitigation_fallback(state, execution)
    
    def _cuad_mitigation_fallback(self, state: IntelligenceState, execution) -> IntelligenceState:
        """Fallback to Phase 1 CUAD tools if Phase 2 fails"""
        try:
            from backend.agents.cuad_mitigation_tools import (
                DeviationDetectorTool, JurisdictionAdapterTool, PrecedentMatcherTool
            )
            
            clauses_json = json.dumps(state["extracted_clauses"])
            
            deviation_tool = DeviationDetectorTool()
            deviations = json.loads(deviation_tool._run(clauses_json))
            
            jurisdiction_tool = JurisdictionAdapterTool()
            jurisdiction_info = json.loads(jurisdiction_tool._run(state["contract_text"]))
            
            precedent_tool = PrecedentMatcherTool()
            precedent_matches = json.loads(precedent_tool._run(clauses_json))
            
            enhanced_violations = state["policy_violations"] + deviations
            enhanced_risk_data = dict(state["risk_data"])
            
            workflow_tracker.complete_agent(execution, f"Fallback completed: {len(deviations)} deviations")
            
            return {**state,
                "policy_violations": enhanced_violations,
                "risk_data": enhanced_risk_data,
                "cuad_deviations": deviations,
                "jurisdiction_info": jurisdiction_info,
                "precedent_matches": precedent_matches,
                "current_step": "cuad_mitigation"
            }
            
        except Exception as fallback_error:
            workflow_tracker.error_agent(execution, f"Fallback also failed: {fallback_error}")
            return {**state,
                "cuad_deviations": [],
                "jurisdiction_info": {},
                "precedent_matches": []
            }
    
    def analyze_contract(self, contract_text: str, use_planning: bool = True, contract_id: Optional[str] = None, tenant_id: Optional[str] = None, contract_type: Optional[str] = None) -> dict:
        """Run analysis with optional autonomous planning"""
        try:
            if use_planning:
                try:
                    # Use asyncio.run with proper event loop handling
                    import asyncio
                    try:
                        # Try to get current loop
                        loop = asyncio.get_running_loop()
                        # If we're in an event loop, create a task
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(asyncio.run, self._analyze_with_planning(contract_text, contract_id, tenant_id, contract_type))
                            return future.result()
                    except RuntimeError:
                        # No event loop running, safe to use asyncio.run
                        return asyncio.run(self._analyze_with_planning(contract_text, contract_id, tenant_id, contract_type))
                except Exception as planning_error:
                    logger.error(f"Planning agent failed: {planning_error}, falling back to traditional workflow")
                    return self._analyze_traditional(
                        contract_text,
                        contract_id,
                        tenant_id,
                        contract_type,
                        execution_path="langgraph_traditional_fallback",
                    )
            else:
                return self._analyze_traditional(
                    contract_text,
                    contract_id,
                    tenant_id,
                    contract_type,
                    execution_path="langgraph_traditional_explicit",
                )
            
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {
                "clauses": [],
                "violations": [],
                "risk_assessment": {"overall_risk_score": 0, "risk_level": "UNKNOWN"},
                "redlines": [],
                "processing_complete": False,
                "planned_execution": None,
                "execution_path": "analysis_failed",
            }
    
    async def _analyze_with_planning(self, contract_text: str, contract_id: Optional[str] = None, tenant_id: Optional[str] = None, contract_type: Optional[str] = None) -> dict:
        """Analyze contract using autonomous planning agent"""
        logger.info("🧠 STEP 1: Starting Planning Agent Analysis")

        # Real bug found live: execute_plan's own comment claims "planning
        # agent already started it" and unconditionally calls workflow_
        # tracker.complete_workflow() at the end - but nothing in this
        # method (or PlanningAgent) ever called start_workflow(). Every
        # planning-path run raised "unsupported operand type(s) for -:
        # 'datetime.datetime' and 'NoneType'" inside complete_workflow()
        # (workflow_start_time was still None), which the outer except
        # here silently converted into a full fallback to the traditional
        # workflow - so the planning path (the documented default,
        # use_planning=True everywhere) had never actually completed a
        # single real analysis; every result looked plausible only because
        # the fallback produces a structurally valid one. start_agent/
        # complete_agent (used throughout this method) track individual
        # agents, not the overall workflow timer - a different pair of
        # methods on the same tracker, confused here.
        workflow_tracker.start_workflow()

        try:
            # Step 1: Track planning agent
            planning_execution = workflow_tracker.start_agent(
                "Autonomous Planning Agent",
                "Analyze query and create optimal execution plan",
                "Contract analysis requirements"
            )
            
            # Step 2: Create execution plan
            logger.info("🧠 STEP 2: Creating execution plan")
            query = "Perform comprehensive contract analysis including clause extraction, policy compliance, risk assessment, and redline generation"
            execution_plan = self.planning_agent.create_execution_plan(query)
            logger.info(f"🧠 STEP 3: Plan created with {len(execution_plan.steps)} steps")
            
            # Complete planning agent tracking with detailed plan info
            step_details = " → ".join([f"{step.step_type.value.replace('_', ' ').title()}" for step in execution_plan.steps])
            workflow_tracker.complete_agent(
                planning_execution, 
                f"Created {execution_plan.strategy} plan: {step_details} (Est: {execution_plan.estimated_duration}s)"
            )
            
            # Step 2: Execute the planned workflow
            logger.info("🧠 STEP 4: Starting plan execution")
            results = await self.execution_engine.execute_plan(execution_plan, contract_text, contract_id=contract_id, tenant_id=tenant_id, contract_type=contract_type)
            logger.info(f"🧠 STEP 5: Plan execution completed: {results.get('processing_complete')}")
            
            # Step 3: Provide feedback
            logger.info("🧠 STEP 6: Providing feedback to planning agent")
            success_rate = 1.0 if results.get("processing_complete") else 0.0
            self.planning_agent.adapt_plan_from_feedback(execution_plan.plan_id, {"success_rate": success_rate})
            
            logger.info("🧠 STEP 7: Planning agent analysis completed successfully")
            return results
            
        except Exception as e:
            # Mark planning agent as failed if we have the execution reference
            try:
                workflow_tracker.error_agent(planning_execution, f"Planning failed: {str(e)}")
            except:
                pass  # planning_execution might not be defined if error occurred early
            
            logger.error(f"🧠 PLANNING AGENT ERROR at step: {e}")
            import traceback
            logger.error(f"🧠 Full traceback: {traceback.format_exc()}")
            raise e
    
    def _analyze_traditional(
        self,
        contract_text: str,
        contract_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        contract_type: Optional[str] = None,
        execution_path: str = "langgraph_traditional_explicit",
    ) -> dict:
        """Traditional workflow analysis (fallback)"""
        # Start workflow tracking
        workflow_tracker.start_workflow()

        # Initialize proper state with CUAD fields
        initial_state = {
            "contract_text": contract_text,
            "contract_id": contract_id,
            "tenant_id": tenant_id,
            "contract_type": contract_type,
            "extracted_clauses": [],
            "policy_violations": [],
            "risk_data": {},
            "redline_suggestions": [],
            "cuad_deviations": [],
            "jurisdiction_info": {},
            "precedent_matches": [],
            "messages": [],
            "current_step": "",
            "processing_result": None,
            "is_complete": False,
            "node_status": {},
        }

        # Run workflow
        final_state = self.workflow.invoke(initial_state)

        # Complete workflow tracking
        workflow_tracker.complete_workflow()

        node_status = final_state.get("node_status", {})
        # "partial" (e.g. policy_checking: some clauses failed to evaluate)
        # must also prevent a dishonest "completed" result, not just a hard
        # "error" - a caller can't tell a genuine clean result from one with
        # gaps otherwise.
        processing_complete = bool(final_state["is_complete"]) and not any(v in ("error", "partial") for v in node_status.values())

        # Return structured results with CUAD data and validation
        return {
            "clauses": final_state["extracted_clauses"],
            "violations": final_state["policy_violations"],
            "risk_assessment": final_state["risk_data"],
            "redlines": final_state["redline_suggestions"],
            "cuad_deviations": final_state.get("cuad_deviations", []),
            "jurisdiction_info": final_state.get("jurisdiction_info", {}),
            "precedent_matches": final_state.get("precedent_matches", []),
            "validation_result": final_state.get("validation_result"),
            "node_status": node_status,
            "processing_complete": processing_complete,
            "planned_execution": False,
            "execution_path": execution_path,
        }

class ContractIntelligenceAgentFactory:
    """Factory following proper design patterns"""
    
    @staticmethod
    def create_orchestrator(llm):
        """Create orchestrator with proper architecture"""
        return IntelligenceOrchestrator(llm)
