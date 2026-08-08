from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
import datetime
from backend.shared.utils.logger import get_logger, correlation_id_var

logger = get_logger(__name__)

class GuardStatus(str, Enum):
    """Externally meaningful validation outcomes.

    `is_safe` remains for compatibility with existing callers, while `status`
    prevents infrastructure failures from being represented as successful
    validation.  Timeout/cancellation/empty are deliberately distinct from a
    policy rejection so chat persistence and clients can tell them apart.
    """

    PASSED = "passed"
    REJECTED = "rejected"
    VALIDATION_FAILED = "validation_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    EMPTY = "empty"


@dataclass
class GuardResult:
    """Result of a guard validation"""
    is_safe: bool
    violation_type: Optional[str] = None
    message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: Optional[GuardStatus] = None
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")

    def __post_init__(self):
        if self.status is None:
            self.status = GuardStatus.PASSED if self.is_safe else GuardStatus.REJECTED

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

    async def avalidate(
        self,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> GuardResult:
        """Async validation hook; deterministic validators remain synchronous."""
        return self.validate(input_text, context)

    def _validate_safely(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        """Run exactly this validator and convert crashes to a closed result."""
        try:
            result = self.validate(input_text, context)
        except Exception as exc:
            # Validator/provider exceptions can contain the complete prompt or
            # contract source in their message.  Record only bounded type and
            # validator identity, and fail closed.  asyncio.CancelledError is a
            # BaseException on supported Python versions and still propagates.
            validator_name = type(self).__name__
            logger.error(
                f"{validator_name} infrastructure failure ({type(exc).__name__}); content omitted"
            )
            result = GuardResult(
                is_safe=False,
                status=GuardStatus.VALIDATION_FAILED,
                violation_type="VALIDATOR_INFRASTRUCTURE_FAILURE",
                message="Output validation could not be completed.",
                metadata={
                    "validator": validator_name,
                    "failure_category": "infrastructure",
                    "exception_type": type(exc).__name__,
                },
            )

        if not isinstance(result, GuardResult):
            validator_name = type(self).__name__
            logger.error(f"{validator_name} returned an invalid result; content omitted")
            return GuardResult(
                is_safe=False,
                status=GuardStatus.VALIDATION_FAILED,
                violation_type="INVALID_VALIDATOR_RESULT",
                message="Output validation could not be completed.",
                metadata={
                    "validator": validator_name,
                    "failure_category": "invalid_result",
                },
            )
        return result

    def validate_chain(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        """Execute the validation chain with rejection short-circuiting."""
        result = self._validate_safely(input_text, context)
        
        if not result.is_safe:
            # Short-circuit if a violation is found
            logger.warning(
                f"Security violation detected: {result.violation_type}; content omitted"
            )
            return result
        
        if self.next_validator:
            return self.next_validator.validate_chain(input_text, context)
        
        return result

    async def _avalidate_safely(
        self,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> GuardResult:
        try:
            result = await self.avalidate(input_text, context)
        except Exception as exc:
            validator_name = type(self).__name__
            logger.error(
                f"{validator_name} infrastructure failure ({type(exc).__name__}); content omitted"
            )
            result = GuardResult(
                is_safe=False,
                status=GuardStatus.VALIDATION_FAILED,
                violation_type="VALIDATOR_INFRASTRUCTURE_FAILURE",
                message="Output validation could not be completed.",
                metadata={
                    "validator": validator_name,
                    "failure_category": "infrastructure",
                    "exception_type": type(exc).__name__,
                },
            )
        if not isinstance(result, GuardResult):
            validator_name = type(self).__name__
            logger.error(f"{validator_name} returned an invalid result; content omitted")
            return GuardResult(
                is_safe=False,
                status=GuardStatus.VALIDATION_FAILED,
                violation_type="INVALID_VALIDATOR_RESULT",
                message="Output validation could not be completed.",
                metadata={
                    "validator": validator_name,
                    "failure_category": "invalid_result",
                },
            )
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
        result = (
            self._validate_every_validator(content, context_metadata)
            if getattr(self, "evaluate_all_validators", False)
            else self.root_validator.validate_chain(content, context_metadata)
        )
        return self._record_validation_result(result, guard_name, context_metadata)

    async def _aexecute_validation(
        self,
        content: str,
        guard_name: str,
        context_metadata: Optional[Dict[str, Any]] = None,
    ) -> GuardResult:
        """Cancellation-aware counterpart used by model-backed Output Guard."""
        logger.info(f"Executing {guard_name} for content length {len(content)}")
        result = await self._avalidate_every_validator(content, context_metadata)
        return self._record_validation_result(result, guard_name, context_metadata)

    def _record_validation_result(
        self,
        result: GuardResult,
        guard_name: str,
        context_metadata: Optional[Dict[str, Any]],
    ) -> GuardResult:
        if result.is_safe:
            logger.info(f"{guard_name}: Validation passed.")
        else:
            logger.error(f"{guard_name}: Validation FAILED. Type: {result.violation_type}")
            
            # Persistent Audit Logging
            if self.audit_logger:
                try:
                    from backend.infrastructure.audit_logger import AuditEventType
                    corr_id = correlation_id_var.get()
                    # Never merge caller context wholesale: Output Guard context
                    # can contain `source_text` with the full retrieved contract.
                    # Keep only bounded identity/status fields plus validator
                    # metadata that validators construct without source content.
                    safe_result_metadata = {
                        key: result.metadata.get(key)
                        for key in (
                            "validator",
                            "failure_category",
                            "exception_type",
                            "category",
                        )
                        if result.metadata.get(key) is not None
                    }
                    if isinstance(result.metadata.get("validator_results"), list):
                        safe_result_metadata["validator_results"] = [
                            {
                                "validator": item.get("validator"),
                                "status": item.get("status"),
                                "violation_type": item.get("violation_type"),
                            }
                            for item in result.metadata["validator_results"]
                            if isinstance(item, dict)
                        ]
                    audit_metadata = {
                        "guard": guard_name,
                        "violation_type": result.violation_type,
                        "validation_status": result.status.value,
                        "correlation_id": corr_id,
                        **safe_result_metadata,
                    }
                    infrastructure_failure = result.status in {
                        GuardStatus.VALIDATION_FAILED,
                        GuardStatus.TIMED_OUT,
                    }
                    self.audit_logger.log_event(
                        event_type=(
                            AuditEventType.VALIDATION_FAILURE
                            if infrastructure_failure
                            else AuditEventType.SECURITY_VIOLATION
                        ),
                        resource_id=corr_id or "unknown",
                        action=f"{guard_name}_denied",
                        tenant_id=(context_metadata or {}).get("tenant_id"),
                        user_id=(context_metadata or {}).get("user_id") or "authenticated_user",
                        status=result.status.value,
                        metadata=audit_metadata
                    )
                except Exception as e:
                    logger.error(f"Failed to audit security violation: {e}")
            
        return result

    def _validate_every_validator(
        self,
        content: str,
        context_metadata: Optional[Dict[str, Any]],
    ) -> GuardResult:
        """Run the full sequential chain and deterministically aggregate it.

        Output Guard uses this so a later pass cannot overwrite an earlier
        rejection/failure and each required validator has a recorded outcome.
        Prompt Guard retains security short-circuiting to avoid sending an
        already-rejected malicious prompt into later model-backed validators.
        """
        results = []
        validator = self.root_validator
        while validator:
            result = validator._validate_safely(content, context_metadata)
            results.append((type(validator).__name__, result))
            validator = validator.next_validator

        return self._aggregate_validator_results(results)

    async def _avalidate_every_validator(
        self,
        content: str,
        context_metadata: Optional[Dict[str, Any]],
    ) -> GuardResult:
        results = []
        validator = self.root_validator
        while validator:
            result = await validator._avalidate_safely(content, context_metadata)
            results.append((type(validator).__name__, result))
            validator = validator.next_validator
        return self._aggregate_validator_results(results)

    @staticmethod
    def _aggregate_validator_results(results) -> GuardResult:
        priority = {
            GuardStatus.VALIDATION_FAILED: 5,
            GuardStatus.TIMED_OUT: 4,
            GuardStatus.REJECTED: 3,
            GuardStatus.EMPTY: 2,
            GuardStatus.CANCELLED: 1,
            GuardStatus.PASSED: 0,
        }
        chosen_name, chosen = max(results, key=lambda item: priority[item[1].status])
        metadata = dict(chosen.metadata)
        metadata["validator_results"] = [
            {
                "validator": name,
                "status": result.status.value,
                "violation_type": result.violation_type,
            }
            for name, result in results
        ]
        metadata.setdefault("validator", chosen_name)
        return GuardResult(
            is_safe=all(result.status == GuardStatus.PASSED for _, result in results),
            status=chosen.status,
            violation_type=chosen.violation_type,
            message=chosen.message,
            metadata=metadata,
        )
