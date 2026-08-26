from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime

@dataclass
class DocumentProcessingRequest:
    file_path: str
    filename: str
    tenant_id: str
    user_id: Optional[str] = None
    processing_options: Dict[str, Any] = None
    # Real, unique, UUID-based id generated upfront by document_upload.py
    # (contract_repository.generate_contract_id()) - threaded through so
    # store_contract uses this exact id instead of minting its own, and so
    # it matches the id Step 5.5's chunking already used. See
    # Neo4jContractRepository.store_contract's contract_id docstring for
    # the real bug this fixes.
    contract_id: Optional[str] = None

@dataclass 
class ContractExtractionResult:
    contract_data: Dict[str, Any]
    confidence_score: float
    validation_errors: List[str]
    requires_human_review: bool
    extracted_text: str = ""
    tenant_id: str = ""

# Domain interfaces (Interface Segregation Principle)
class ITextExtractor(ABC):
    @abstractmethod
    def extract_text(self, file_path: str) -> str:
        pass

class IContractAnalyzer(ABC):
    @abstractmethod
    async def analyze_contract(self, text: str) -> Dict[str, Any]:
        pass

class IContractRepository(ABC):
    @abstractmethod
    async def store_contract(self, contract_data: Dict[str, Any], tenant_id: str) -> str:
        pass
    
    @abstractmethod
    async def get_contract_by_id(self, contract_id: str, tenant_id: str) -> Dict[str, Any]:
        pass

class IDocumentProcessor(ABC):
    @abstractmethod
    async def process_document(self, request: DocumentProcessingRequest) -> ContractExtractionResult:
        pass

# Contract Intelligence Entities
@dataclass
class ContractClause:
    clause_type: str  # Payment, Liability, IP, Confidentiality, Termination
    content: str
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL - real baseline computed
    # per-clause (see feedback_learning_system.compute_baseline_risk_level),
    # possibly overridden below by a learned pattern
    confidence_score: float
    location: str = ""
    clause_id: str = ""
    grounded: bool = True  # False if the clause text couldn't be located in the source contract
    # Adaptive-learning fields (AdaptiveAnalyzer._apply_risk_pattern): all
    # None unless a learned pattern from historical LegalDecisions actually
    # matched and adjusted this clause's risk_level above.
    original_risk_level: Optional[str] = None  # risk_level before the adjustment, preserved for traceability
    learned_risk_adjustment: Optional[str] = None  # explicit marker that risk_level came from a learned pattern (duplicates risk_level's new value)
    pattern_confidence: Optional[float] = None
    risk_adjustment_pattern_id: Optional[str] = None  # which historical pattern drove the adjustment

@dataclass
class PolicyViolation:
    clause_type: str
    issue: str
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL
    suggested_fix: str
    clause_content: str = ""
    clause_id: str = ""
    clause_grounded: bool = True

@dataclass
class RiskAssessment:
    overall_risk_score: float  # 0-100
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    critical_issues: List[str]
    recommendations: List[str]
    critical_issue_details: List[Dict[str, Any]] = None
    # Real, itemized contributions to overall_risk_score (base-by-contract-
    # type plus one entry per applied violation) - see RiskCalculatorTool.
    # None here on legacy/pre-Phase-2 data means "not computed", distinct
    # from an empty list meaning "computed, contract had no factors".
    score_breakdown: Optional[List[Dict[str, Any]]] = None

    def __post_init__(self):
        if self.critical_issue_details is None:
            self.critical_issue_details = []

@dataclass
class RedlineRecommendation:
    original_text: str
    suggested_text: str
    justification: str
    priority: str  # LOW, MEDIUM, HIGH

@dataclass
class ContractIntelligence:
    clauses: List[ContractClause]
    violations: List[PolicyViolation]
    risk_assessment: RiskAssessment
    redlines: List[RedlineRecommendation]
    processing_time: float = 0.0
    
    # CUAD mitigation fields (Phase 1)
    cuad_deviations: List[Dict[str, Any]] = None
    jurisdiction_info: Dict[str, Any] = None
    precedent_matches: List[Dict[str, Any]] = None

    # Per-node success/failure status, so callers can distinguish a real
    # result from one masked by a silent partial failure.
    node_status: Dict[str, str] = None
    processing_complete: bool = True

    # A-F quality grade (backend/agents/run_quality_grader.py, computed
    # for real on the traditional LangGraph path), whether this analysis
    # needs human review (any step genuinely "failed", not just
    # "partial"), and which CUAD mitigation tier actually ran (real
    # Phase3->Phase2->Phase1 fallback, previously computed and discarded
    # before reaching here).
    quality_grade: Dict[str, Any] = None
    escalated: bool = False
    analysis_method: Optional[str] = None
    # Identity of the top-level analysis route - separate from
    # analysis_method, which describes the CUAD mitigation tier inside
    # a route.
    execution_path: Optional[str] = None
    planned_execution: Optional[bool] = None

    def __post_init__(self):
        if self.cuad_deviations is None:
            self.cuad_deviations = []
        if self.jurisdiction_info is None:
            self.jurisdiction_info = {}
        if self.precedent_matches is None:
            self.precedent_matches = []
        if self.node_status is None:
            self.node_status = {}
        if self.quality_grade is None:
            self.quality_grade = {}

@dataclass
class AgentMessage:
    agent_id: str
    message_type: str
    data: Dict[str, Any]
    timestamp: str
