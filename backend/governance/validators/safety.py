import json
from typing import List, Optional, Dict, Any
from ..base import GuardStatus, IGuardValidator, GuardResult


def _response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
            if isinstance(block, str) or isinstance(block, dict)
        )
    return ""

class LlamaGuardValidator(IGuardValidator):
    """
    Implements Llama Guard 3 post-check logic to ensure output safety.
    """
    RISK_CATEGORIES = {
        "S1": "Violent Crimes",
        "S2": "Non-Violent Crimes",
        "S3": "Sexually Explicit Content",
        "S4": "Child Sexual Exploitation",
        "S5": "Defamation",
        "S6": "Specialized Advice",
        "S7": "Privacy",
        "S8": "Intellectual Property",
        "S9": "Hate Speech",
        "S10": "Harassment",
        "S11": "Election Integrity",
        "S12": "Criminal Planning"
    }

    def __init__(self):
        super().__init__()
        self._llm_mgr = None

    @property
    def llm_mgr(self):
        if self._llm_mgr is None:
            from backend.llm_manager import LLMManager
            self._llm_mgr = LLMManager()
        return self._llm_mgr

    def _prompt(self, input_text: str) -> str:
        system_instruction = (
            "You are a Llama Guard safety classifier. Analyze the provided AI response "
            "and determine if it violates any of the following safety categories:\n"
            + "\n".join([f"{k}: {v}" for k, v in self.RISK_CATEGORIES.items()]) + "\n"
            "Respond ONLY with a JSON object: {\"is_safe\": boolean, \"violation_category\": \"code or null\", \"reason\": \"string\"}"
        )
        return f"System: {system_instruction}\nAI Output: {input_text}"

    def _result_from_response(self, response: Any) -> GuardResult:
        content = _response_text(response.content).strip()
        if not content:
            raise ValueError("empty validator response")
        if "```json" in content:
            content = content.split("```json")[-1].split("```")[0].strip()

        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("is_safe"), bool):
            raise ValueError("invalid safety validator schema")
        if not data.get("is_safe", True):
            category = data.get("violation_category", "UNKNOWN")
            category_name = self.RISK_CATEGORIES.get(category, "General Safety Violation")
            return GuardResult(
                is_safe=False,
                violation_type="UNSAFE_OUTPUT",
                message=f"Output flagged for {category_name}.",
                metadata={"category": category},
            )
        return GuardResult(is_safe=True)

    def _failure_result(self, exc: Exception) -> GuardResult:
        from backend.shared.utils.logger import get_logger

        get_logger(__name__).error(
            f"Llama Guard validation failed ({type(exc).__name__}); output omitted"
        )
        return GuardResult(
            is_safe=False,
            status=GuardStatus.VALIDATION_FAILED,
            violation_type="VALIDATOR_INFRASTRUCTURE_FAILURE",
            message="Output safety validation could not be completed.",
            metadata={
                "validator": type(self).__name__,
                "failure_category": "infrastructure",
                "exception_type": type(exc).__name__,
            },
        )

    def validate(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        try:
            model = self.llm_mgr.get_raw_model_by_name("gemini-2.5-flash")
            return self._result_from_response(model.invoke(self._prompt(input_text)))
        except Exception as exc:
            return self._failure_result(exc)

    async def avalidate(
        self,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> GuardResult:
        try:
            model = self.llm_mgr.get_raw_model_by_name("gemini-2.5-flash")
            return self._result_from_response(await model.ainvoke(self._prompt(input_text)))
        except Exception as exc:
            return self._failure_result(exc)
