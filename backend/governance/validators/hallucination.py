import json
from typing import Dict, Any, Optional
from ..base import IGuardValidator, GuardResult
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

class HallucinationValidator(IGuardValidator):
    """
    Detects and flags factually incorrect or unsupported AI-generated content.
    Uses an LLM-based "Critic" to compare the output against provided source context.
    """
    
    def __init__(self):
        super().__init__()
        self._llm_mgr = None

    @property
    def llm_mgr(self):
        if self._llm_mgr is None:
            from backend.llm_manager import LLMManager
            self._llm_mgr = LLMManager()
        return self._llm_mgr

    def validate(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        """
        Validate if the AI generated output is supported by the source context.
        """
        if not context or 'source_text' not in context:
            logger.warning("Hallucination check skipped: No source context ('source_text') provided in metadata.")
            return GuardResult(is_safe=True)

        source_text = context.get('source_text', '')
        if not source_text:
            return GuardResult(is_safe=True)

        logger.info("Performing hallucination check against source context...")

        system_instruction = (
            "You are a Fact-Checking Auditor for a Contract Analysis system. "
            "Your task is to determine if the AI-generated 'Response' is strictly "
            "supported by the provided 'Source Text' (the contract).\n\n"
            "Guidelines:\n"
            "1. Flag as hallucination if the Response mentions numbers, dates, or parties "
            "not present in the Source Text.\n"
            "2. Flag as hallucination if the Response makes legal claims or "
            "interpretations that contradict the Source Text.\n"
            "3. If the Response says 'I don't know' or 'The contract does not specify', "
            "that is NOT a hallucination.\n\n"
            "Respond ONLY with a JSON object: {\"is_hallucination\": boolean, \"reason\": \"string\", \"confidence\": float}"
        )

        prompt = (
            f"Source Text (Contract Content):\n{source_text}\n\n"
            f"AI-Generated Response to Verify:\n{input_text}\n"
        )

        try:
            model = self.llm_mgr.get_model_by_name("gemini-2.5-flash")
            response = model.invoke(f"System: {system_instruction}\nPrompt: {prompt}")
            
            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json")[-1].split("```")[0].strip()
            
            data = json.loads(content)
            
            if data.get("is_hallucination", False):
                logger.warning(f"Hallucination detected: {data.get('reason')}")
                return GuardResult(
                    is_safe=False,
                    violation_type="HALLUCINATION_DETECTED",
                    message=f"The assistant's response contains information not supported by the source contract: {data.get('reason')}",
                    metadata={"hallucination_details": data}
                )
        except Exception as e:
            # Some model/runtime exceptions include the complete prompt,
            # which here contains retrieved contract text.  Preserve the
            # actionable failure class without copying sensitive material
            # into logs.
            logger.error(
                f"Hallucination check failed ({type(e).__name__}); prompt and source omitted"
            )
            # In case of failure, we might want to fail-safe (pass) or fail-secure (block).
            # For this implementation, we fail-safe to avoid blocking users on technical errors.
            return GuardResult(is_safe=True)

        return GuardResult(is_safe=True)
