from typing import List, Optional, Dict, Any
from .base import BaseGuard, GuardStatus, IGuardValidator, GuardResult
from .pii_engine import PIIEngine

class OutputGuard(BaseGuard):
    """
    Facade for the output guard system (Llama Guard Post-Check).
    Orchestrates multiple validators to ensure AI generated output safety.
    """
    evaluate_all_validators = True
    def __init__(self, validators=None, audit_logger=None, model_manager=None):
        self.model_manager = model_manager
        super().__init__(validators=validators, audit_logger=audit_logger)

    def _default_chain(self) -> IGuardValidator:
        """Provide a default sensible security chain for outputs"""
        from .validators import LlamaGuardValidator, DomainComplianceValidator, HallucinationValidator
        
        # Order matters: check for high-level safety first, then domain specific rules, then hallucinations
        llama_guard = LlamaGuardValidator(self.model_manager)
        domain_compliance = DomainComplianceValidator()
        hallucination_check = HallucinationValidator(self.model_manager)
        
        llama_guard.set_next(domain_compliance)
        domain_compliance.set_next(hallucination_check)
        return llama_guard

    def validate(self, output: str, context_metadata: Optional[Dict[str, Any]] = None) -> GuardResult:
        """
        Validate an AI generated output against the configured safety chain.
        Also performs automated PII redaction on the final safe output.
        """
        if not output or not output.strip():
            return GuardResult(
                is_safe=False,
                status=GuardStatus.EMPTY,
                violation_type="EMPTY_MODEL_OUTPUT",
                message="The assistant returned no response.",
                metadata={"failure_category": "empty_output"},
            )

        result = self._execute_validation(output, "Output Guard", context_metadata)
        
        # If the content is safe from a policy perspective, still redact any PII
        if result.is_safe:
            redacted_content = PIIEngine.redact(output)
            result.metadata["redacted_content"] = redacted_content
            
        return result

    async def avalidate(
        self,
        output: str,
        context_metadata: Optional[Dict[str, Any]] = None,
    ) -> GuardResult:
        """Cancellation-aware validation for the async SSE request path."""
        if not output or not output.strip():
            return GuardResult(
                is_safe=False,
                status=GuardStatus.EMPTY,
                violation_type="EMPTY_MODEL_OUTPUT",
                message="The assistant returned no response.",
                metadata={"failure_category": "empty_output"},
            )

        result = await self._aexecute_validation(
            output,
            "Output Guard",
            context_metadata,
        )
        if result.is_safe:
            result.metadata["redacted_content"] = PIIEngine.redact(output)
        return result
