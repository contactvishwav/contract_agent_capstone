from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
import datetime
from backend.shared.utils.logger import get_logger, correlation_id_var

logger = get_logger(__name__)

@dataclass
class GuardResult:
    """Result of a guard validation"""
    is_safe: bool
    violation_type: Optional[str] = None
    message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")

class IGuardValidator(ABC):
    """
    Interface for guard validators (Strategy Pattern).
    Supports chaining (Chain of Responsibility Pattern).
    """
    def __init__(self):
        self.next_validator: Optional['IGuardValidator'] = None

    def set_next(self, validator: 'IGuardValidator') -> 'IGuardValidator':
        """Chain the next validator"""
        self.next_validator = validator
        return validator

    @abstractmethod
    def validate(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        """Validate the input/output text with optional context"""
        pass

    def validate_chain(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        """Execute the validation chain"""
        result = self.validate(input_text, context)
        
        if not result.is_safe:
            # Short-circuit if a violation is found
            logger.warning(f"Security violation detected: {result.violation_type} - {result.message}")
            return result
        
        if self.next_validator:
            return self.next_validator.validate_chain(input_text, context)
        
        return result

class BaseGuard(ABC):
    """
    Base class for Guard services (Facade Pattern).
    Implements common logic for building and executing validator chains.
    """
    def __init__(self, validators: Optional[List[IGuardValidator]] = None, audit_logger: Any = None):
        self.audit_logger = audit_logger
        if validators:
            self.root_validator = self._build_chain(validators)
        else:
            self.root_validator = self._default_chain()

    def _build_chain(self, validators: List[IGuardValidator]) -> IGuardValidator:
        """Build a chain from a list of validators"""
        if not validators:
            raise ValueError("At least one validator is required")
        
        for i in range(len(validators) - 1):
            validators[i].set_next(validators[i+1])
        
        return validators[0]

    @abstractmethod
    def _default_chain(self) -> IGuardValidator:
        """Provide a default sensible security chain"""
        pass

    def _execute_validation(self, content: str, guard_name: str, context_metadata: Optional[Dict[str, Any]] = None) -> GuardResult:
        """Shared execution logic with logging and persistent auditing"""
        logger.info(f"Executing {guard_name} for content length {len(content)}")
        result = self.root_validator.validate_chain(content, context_metadata)
        
        if result.is_safe:
            logger.info(f"{guard_name}: Validation passed.")
        else:
            logger.error(f"{guard_name}: Validation FAILED. Type: {result.violation_type}")
            
            # Persistent Audit Logging
            if self.audit_logger:
                try:
                    from backend.infrastructure.audit_logger import AuditEventType
                    corr_id = correlation_id_var.get()
                    audit_metadata = {
                        "guard": guard_name,
                        "violation_type": result.violation_type,
                        "correlation_id": corr_id,
                        **(context_metadata or {})
                    }
                    self.audit_logger.log_event(
                        event_type=AuditEventType.SECURITY_VIOLATION,
                        resource_id=corr_id or "unknown",
                        action=f"{guard_name}_denied",
                        tenant_id=(context_metadata or {}).get("tenant_id"),
                        user_id=(context_metadata or {}).get("user_id") or "authenticated_user",
                        status="violation",
                        metadata=audit_metadata
                    )
                except Exception as e:
                    logger.error(f"Failed to audit security violation: {e}")
            
        return result
