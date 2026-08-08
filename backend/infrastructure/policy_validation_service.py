"""Policy validation service using Chain of Responsibility pattern."""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from backend.agents.supervisor.interfaces import ValidationResult
from backend.infrastructure.content_validator import (
    ContentQualityValidator,
    SecurityValidator,
    ValidationSeverity,
)
from backend.domain.policies.entities import PolicyDocument, PolicyRule


class PolicyValidator(ABC):
    """Abstract base for policy validators using Chain of Responsibility."""
    
    def __init__(self):
        self._next_validator: Optional[PolicyValidator] = None
    
    def set_next(self, validator: 'PolicyValidator') -> 'PolicyValidator':
        """Set next validator in chain."""
        self._next_validator = validator
        return validator
    
    def validate(self, policy_data: Dict[str, Any]) -> ValidationResult:
        """Validate policy and pass to next validator."""
        result = self._validate(policy_data)
        
        if result.passed and self._next_validator:
            return self._next_validator.validate(policy_data)
        
        return result
    
    @abstractmethod
    def _validate(self, policy_data: Dict[str, Any]) -> ValidationResult:
        """Implement specific validation logic."""
        pass


class PolicyStructureValidator(PolicyValidator):
    """Validates policy document structure using existing patterns."""
    
    def _validate(self, policy_data: Dict[str, Any]) -> ValidationResult:
        """Validate policy structure."""
        required_fields = ['policy_text', 'tenant_id', 'policy_name']
        
        for field in required_fields:
            if field not in policy_data or not policy_data[field]:
                return ValidationResult(
                    passed=False,
                    score=0.0,
                    message=f"Missing required field: {field}",
                    details={'missing_field': field}
                )
        
        # Check minimum content length
        policy_text = policy_data['policy_text']
        if len(policy_text) < 100:
            return ValidationResult(
                passed=False,
                score=0.3,
                message="Policy text too short (minimum 100 characters)",
                details={'text_length': len(policy_text)}
            )
        
        return ValidationResult(
            passed=True,
            score=1.0,
            message="Policy structure validation passed",
            details={'text_length': len(policy_text)}
        )


class PolicyContentValidator(PolicyValidator):
    """Validates policy content quality (length, garbled text, PII).

    Real, confirmed bug found live while verifying the asyncio.run() fix
    above: this used to call ContentValidationService.validate_file_upload(
    {'content': ..., 'file_type': 'policy', 'tenant_id': ...}) -
    validate_file_upload's own docstring says "filename, size only", and
    its FileTypeValidator unconditionally requires a `.pdf` filename
    (data.get("filename", "")) that this code never had to give it (a
    policy upload here is raw text + tenant_id, not a file with a real
    filename) - so it always failed with "Invalid file type. Allowed:
    .pdf" against an empty filename, for every policy document,
    regardless of content. This was itself masked by a second,
    independent bug: `validation_result.get('valid', False)` checked a
    key named 'valid', but validate_file_upload() actually returns the
    key 'is_valid' - so this branch always fell through to the `False`
    default anyway, even on the rare case the file-type check might not
    have fired. Net effect: policy content validation failed 100% of the
    time, for every document. Fixed by validating the actual text content
    directly - ContentQualityValidator (length/garbled-text) and
    SecurityValidator (PII, via the same PIIEngine the chat path uses),
    neither of which need a filename - instead of a file-metadata
    validator that was never given file metadata.
    """

    def __init__(self):
        super().__init__()
        self.quality_validator = ContentQualityValidator(min_length=100)
        self.security_validator = SecurityValidator()

    def _validate(self, policy_data: Dict[str, Any]) -> ValidationResult:
        """Validate policy content quality."""
        policy_text = policy_data['policy_text']
        content_data = {'full_text': policy_text}

        results = self.quality_validator.validate(content_data) + self.security_validator.validate(content_data)
        blocking = [
            r for r in results
            if not r.is_valid and r.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL)
        ]

        if blocking:
            return ValidationResult(
                passed=False,
                score=0.4,
                message=f"Content validation failed: {'; '.join(r.message for r in blocking)}",
                details={'results': [r.to_dict() for r in results]}
            )
        
        # Check for policy-specific patterns
        policy_indicators = ['shall', 'must', 'required', 'prohibited', 'mandatory']
        found_indicators = sum(1 for indicator in policy_indicators if indicator.lower() in policy_text.lower())
        
        if found_indicators < 2:
            return ValidationResult(
                passed=False,
                score=0.6,
                message="Policy text lacks sufficient policy language indicators",
                details={'indicators_found': found_indicators, 'minimum_required': 2}
            )
        
        return ValidationResult(
            passed=True,
            score=0.9,
            message="Policy content validation passed",
            details={'indicators_found': found_indicators}
        )


class PolicyRuleValidator(PolicyValidator):
    """Validates extracted policy rules."""
    
    def _validate(self, policy_data: Dict[str, Any]) -> ValidationResult:
        """Validate policy rules after extraction."""
        # This validator runs after rule extraction
        rules = policy_data.get('extracted_rules', [])
        
        if not rules:
            return ValidationResult(
                passed=False,
                score=0.2,
                message="No policy rules could be extracted",
                details={'rules_count': 0}
            )
        
        # Validate rule quality
        valid_rules = 0
        for rule in rules:
            if (rule.get('rule_text') and 
                len(rule.get('rule_text', '')) > 20 and
                rule.get('rule_type') in ['mandatory', 'prohibited', 'recommended']):
                valid_rules += 1
        
        quality_score = valid_rules / len(rules) if rules else 0
        
        if quality_score < 0.5:
            return ValidationResult(
                passed=False,
                score=quality_score,
                message=f"Low rule quality: {valid_rules}/{len(rules)} rules are valid",
                details={'valid_rules': valid_rules, 'total_rules': len(rules)}
            )
        
        return ValidationResult(
            passed=True,
            score=quality_score,
            message=f"Policy rules validation passed: {valid_rules}/{len(rules)} valid rules",
            details={'valid_rules': valid_rules, 'total_rules': len(rules)}
        )


class PolicyValidationService:
    """Service for validating policies using Chain of Responsibility."""

    def __init__(self):
        # Build validation chain
        self.structure_validator = PolicyStructureValidator()
        self.content_validator = PolicyContentValidator()
        self.rule_validator = PolicyRuleValidator()

        # Chain validators. rule_validator is deliberately NOT chained here -
        # it validates extracted_rules, which only exists after rule
        # extraction has already happened (see validate_policy_rules below,
        # the only real usage - PolicyRuleValidator.validate is a separate,
        # standalone entry point). A real, confirmed bug found live during
        # production verification: this used to chain structure -> content
        # -> rule together, so validate_policy_upload (called once, before
        # any extraction has run) always cascaded into rule_validator with
        # an empty extracted_rules, unconditionally failing with "No policy
        # rules could be extracted" - invisible until just now because
        # PolicyContentValidator itself always failed first (see its own
        # docstring), so the chain never actually reached rule_validator
        # until that bug was fixed.
        self.structure_validator.set_next(self.content_validator)
    
    def validate_policy_upload(self, policy_data: Dict[str, Any]) -> ValidationResult:
        """Validate policy for upload."""
        return self.structure_validator.validate(policy_data)
    
    def validate_policy_rules(self, policy_data: Dict[str, Any], extracted_rules: List[Dict[str, Any]]) -> ValidationResult:
        """Validate extracted policy rules."""
        policy_data_with_rules = {**policy_data, 'extracted_rules': extracted_rules}
        return self.rule_validator.validate(policy_data_with_rules)
    
    def get_validation_summary(self, policy_data: Dict[str, Any]) -> Dict[str, Any]:
        """Get comprehensive validation summary."""
        results = []
        current_validator = self.structure_validator
        
        while current_validator:
            result = current_validator._validate(policy_data)
            results.append({
                'validator': current_validator.__class__.__name__,
                'passed': result.passed,
                'score': result.score,
                'message': result.message,
                'details': result.details
            })
            current_validator = current_validator._next_validator
        
        overall_score = sum(r['score'] for r in results) / len(results) if results else 0
        all_passed = all(r['passed'] for r in results)
        
        return {
            'overall_passed': all_passed,
            'overall_score': overall_score,
            'validation_results': results,
            'summary': f"{'Passed' if all_passed else 'Failed'} with score {overall_score:.2f}"
        }