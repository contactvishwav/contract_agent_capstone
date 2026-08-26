from typing import TypedDict, List, Any, Optional
from backend.domain.value_objects import ProcessingResult

class IntelligenceState(TypedDict):
    """Properly designed state following SRP - data separate from workflow"""

    # Input data
    contract_text: str
    contract_id: Optional[str]
    tenant_id: Optional[str]
    contract_type: Optional[str]
    
    # Processing results (structured data, not strings)
    extracted_clauses: List[dict]
    policy_violations: List[dict] 
    risk_data: dict
    redline_suggestions: List[dict]
    
    # CUAD mitigation results (Phase 1 extension)
    cuad_deviations: List[dict]
    jurisdiction_info: dict
    precedent_matches: List[dict]
    # Real, confirmed bug found live this session: cuad_mitigation's 3 real
    # tiers have set this key on every success return since before this
    # session started (Phase 3) and since this session's Phase 2/Phase 1
    # validation wiring - but it was never declared here. Undeclared keys
    # don't reliably survive the real Redis checkpointer boundary
    # (human_review_gate's pause/resume - _get_redis_checkpointer), so a
    # resumed run's final_state silently lost it even though the node
    # itself genuinely computed a real value (confirmed via real logs:
    # "[validated: False, confidence: 0.29]" printed, but final_state.get(
    # "validation_result") came back None). run_quality_grader.py's grading
    # depends on this key actually reaching _assemble_traditional_result.
    validation_result: Optional[Any]
    
    # Workflow metadata
    messages: List[Any]
    current_step: str
    processing_result: ProcessingResult
    is_complete: bool
    node_status: dict