import unittest
from types import SimpleNamespace

from backend.governance.validators.hallucination import HallucinationValidator
from backend.governance.validators.safety import LlamaGuardValidator
from backend.governance.base import GuardStatus


SENSITIVE_MARKER = "CONFIDENTIAL-CONTRACT-TEXT-DO-NOT-LOG"


class _ExplodingModel:
    def invoke(self, _prompt):
        raise RuntimeError(f"provider echoed prompt: {SENSITIVE_MARKER}")


def _manager():
    return SimpleNamespace(get_raw_model_by_name=lambda _name: _ExplodingModel())


class GuardSensitiveLoggingTests(unittest.TestCase):
    def test_safety_validator_omits_provider_exception_content(self):
        validator = LlamaGuardValidator()
        validator._llm_mgr = _manager()

        with self.assertLogs("backend.governance.validators.safety", level="ERROR") as logs:
            result = validator.validate(SENSITIVE_MARKER)

        self.assertFalse(result.is_safe)
        self.assertEqual(result.status, GuardStatus.VALIDATION_FAILED)
        rendered = "\n".join(logs.output)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn(SENSITIVE_MARKER, rendered)

    def test_hallucination_validator_omits_prompt_and_source(self):
        validator = HallucinationValidator()
        validator._llm_mgr = _manager()

        with self.assertLogs("backend.governance.validators.hallucination", level="ERROR") as logs:
            result = validator.validate("answer", {"source_text": SENSITIVE_MARKER})

        self.assertFalse(result.is_safe)
        self.assertEqual(result.status, GuardStatus.VALIDATION_FAILED)
        rendered = "\n".join(logs.output)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn(SENSITIVE_MARKER, rendered)


if __name__ == "__main__":
    unittest.main()
