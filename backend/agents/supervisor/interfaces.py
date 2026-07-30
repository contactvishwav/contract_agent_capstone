from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum

if TYPE_CHECKING:
    from backend.agents.supervisor.workflow_engine import Workflow

class MessageType(Enum):
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"

@dataclass
class WorkflowContext:
    workflow_id: str
    workflow_type: str
    context_data: Dict[str, Any] = None
    correlation_id: str = ""

@dataclass
class AgentContext:
    input_data: Dict[str, Any]
    workflow_context: Optional['WorkflowContext'] = None
    correlation_id: str = ""

@dataclass
class AgentResult:
    status: str
    data: Dict[str, Any]
    confidence: float = 0.0
    agent_id: str = ""

@dataclass
class AgentMessage:
    sender: str
    receiver: str
    content: Dict[str, Any]
    message_type: MessageType
    correlation_id: str

@dataclass
class ValidationResult:
    passed: bool
    score: float
    message: str
    details: Dict[str, Any] = None

@dataclass
class QualityReport:
    validation: ValidationResult
    score: float
    grade: str
    recommendations: List[str]

class IAgent(ABC):
    @abstractmethod
    def execute(self, context: AgentContext) -> AgentResult:
        pass
    
    @abstractmethod
    def get_capabilities(self) -> List[str]:
        pass

class IValidationStrategy(ABC):
    @abstractmethod
    def validate(self, result: AgentResult) -> ValidationResult:
        pass

class IConsensusStrategy(ABC):
    @abstractmethod
    def reach_consensus(self, opinions: List[AgentResult]) -> AgentResult:
        pass

class IAgentRegistry(ABC):
    @abstractmethod
    def register_agent(self, agent_id: str, agent: IAgent):
        pass
    
    @abstractmethod
    def get_agent(self, agent_id: str) -> Optional[IAgent]:
        pass

class IWorkflowEngine(ABC):
    @abstractmethod
    def create_workflow(self, workflow_type: str) -> 'Workflow':
        pass

class IQualityManager(ABC):
    @abstractmethod
    def validate_agent_output(self, agent_type: str, result: AgentResult) -> QualityReport:
        pass

class IMessageBus(ABC):
    @abstractmethod
    def publish(self, message: AgentMessage):
        pass
    
    @abstractmethod
    def subscribe(self, agent_id: str, handler):
        pass