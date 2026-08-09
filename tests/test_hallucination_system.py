import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.governance.validators.hallucination import HallucinationValidator
from backend.governance.base import GuardResult

def test_hallucination_detection():
    validator = HallucinationValidator()
    
    # Mock source context
    source_context = {
        "source_text": "This Agreement is entered into on January 1, 2024, by and between Alpha Corp and Beta Inc."
    }
    
    # 1. Test supported content
    safe_output = "The agreement is between Alpha Corp and Beta Inc and was signed in early 2024."
    # We'll mock the LLM call to return no hallucination
    validator._llm_mgr = MagicMock()
    mock_model = MagicMock()
    validator._llm_mgr.get_raw_model_by_name.return_value = mock_model
    
    mock_model.invoke.return_value.content = '{"decision":"supported","reason_category":"supported","unsupported_material_claims":0,"confidence":0.95}'
    
    result = validator.validate(safe_output, source_context)
    print(f"Safe Output Result: is_safe={result.is_safe}")
    assert result.is_safe == True

    # 2. Test hallucinated content
    hallucinated_output = "The agreement includes a $50,000 termination fee for Gamma LLC."
    mock_model.invoke.return_value.content = '{"decision":"unsupported","reason_category":"unsupported_claim","unsupported_material_claims":2,"confidence":0.98}'
    
    result = validator.validate(hallucinated_output, source_context)
    print(f"Hallucinated Output Result: is_safe={result.is_safe}, violation={result.violation_type}")
    assert result.is_safe == False
    assert result.violation_type == "HALLUCINATION_DETECTED"

if __name__ == "__main__":
    test_hallucination_detection()
    print("Verification tests passed!")
