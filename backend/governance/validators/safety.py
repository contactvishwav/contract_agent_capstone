import json
from typing import List, Optional, Dict, Any
from ..base import IGuardValidator, GuardResult

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

    def validate(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        system_instruction = (
            "You are a Llama Guard safety classifier. Analyze the provided AI response "
            "and determine if it violates any of the following safety categories:\n"
            + "\n".join([f"{k}: {v}" for k, v in self.RISK_CATEGORIES.items()]) + "\n"
            "Respond ONLY with a JSON object: {\"is_safe\": boolean, \"violation_category\": \"code or null\", \"reason\": \"string\"}"
        )
        
        try:
            model = self.llm_mgr.get_model_by_name("gemini-2.5-flash")
            response = model.invoke(f"System: {system_instruction}\nAI Output: {input_text}")
            
            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json")[-1].split("```")[0].strip()
            
            data = json.loads(content)
            
            if not data.get("is_safe", True):
                category = data.get("violation_category", "UNKNOWN")
                category_name = self.RISK_CATEGORIES.get(category, "General Safety Violation")
                return GuardResult(
                    is_safe=False,
                    violation_type="UNSAFE_OUTPUT",
                    message=f"Output flagged for {category_name}: {data.get('reason')}",
                    metadata={"category": category}
                )
        except Exception as e:
            from backend.shared.utils.logger import get_logger
            # Provider/LangGraph exceptions can embed the entire invocation
            # (system instruction plus the contract-derived output).  Log
            # only the exception class; raw prompts and legal text are never
            # safe operational-log fields.
            get_logger(__name__).error(
                f"Llama Guard validation failed ({type(e).__name__}); output omitted"
            )
            pass
            
        return GuardResult(is_safe=True)
