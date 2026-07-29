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
    risk_level: str  # LOW, MEDIUM, HIGH
    confidence_score: float
    location: str = ""
    clause_id: str = ""
    grounded: bool = True  # False if the clause text couldn't be located in the source contract

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

    def __post_init__(self):
        if self.cuad_deviations is None:
            self.cuad_deviations = []
        if self.jurisdiction_info is None:
            self.jurisdiction_info = {}
        if self.precedent_matches is None:
            self.precedent_matches = []
        if self.node_status is None:
            self.node_status = {}

@dataclass
class AgentMessage:
    agent_id: str
    message_type: str
    data: Dict[str, Any]
    timestamp: str