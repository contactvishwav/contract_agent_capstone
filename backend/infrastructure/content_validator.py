"""
Advanced Content Validation
Chain of Responsibility + Strategy Pattern for document validation
"""

import logging
from collections import defaultdict
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod
from enum import Enum

from backend.governance.pii_engine import PIIEngine
from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class ValidationSeverity(Enum):
    """Validation severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class ValidationResult:
    """Validation result with details"""
    def __init__(self, is_valid: bool, severity: ValidationSeverity, message: str, details: Optional[Dict] = None):
        self.is_valid = is_valid
        self.severity = severity
        self.message = message
        self.details = details or {}
        self.timestamp = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details
        }

class IValidator(ABC):
    """Base validator interface - Strategy Pattern"""
    
    def __init__(self):
        self.next_validator: Optional[IValidator] = None
    
    def set_next(self, validator: 'IValidator') -> 'IValidator':
        """Chain of Responsibility pattern"""
        self.next_validator = validator
        return validator
    
    @abstractmethod
    def validate(self, data: Dict[str, Any]) -> List[ValidationResult]:
        """Validate data and return results"""
        pass
    
    def validate_chain(self, data: Dict[str, Any]) -> List[ValidationResult]:
        """Execute validation chain"""
        results = self.validate(data)
        
        if self.next_validator:
            results.extend(self.next_validator.validate_chain(data))
        
        return results

class FileSizeValidator(IValidator):
    """Validate file size constraints"""
    
    def __init__(self, max_size_mb: int = 50):
        super().__init__()
        self.max_size_mb = max_size_mb
    
    def validate(self, data: Dict[str, Any]) -> List[ValidationResult]:
        file_size = data.get("file_size", 0)
        max_bytes = self.max_size_mb * 1024 * 1024
        
        if file_size > max_bytes:
            return [ValidationResult(
                is_valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"File size {file_size / 1024 / 1024:.2f}MB exceeds maximum {self.max_size_mb}MB",
                details={"file_size": file_size, "max_size": max_bytes}
            )]
        
        return [ValidationResult(
            is_valid=True,
            severity=ValidationSeverity.INFO,
            message="File size validation passed",
            details={"file_size": file_size}
        )]

class FileTypeValidator(IValidator):
    """Validate file type and extension"""
    
    def __init__(self, allowed_types: List[str] = None):
        super().__init__()
        self.allowed_types = allowed_types or [".pdf"]
    
    def validate(self, data: Dict[str, Any]) -> List[ValidationResult]:
        filename = data.get("filename", "")
        
        if not any(filename.lower().endswith(ext) for ext in self.allowed_types):
            return [ValidationResult(
                is_valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Invalid file type. Allowed: {', '.join(self.allowed_types)}",
                details={"filename": filename, "allowed_types": self.allowed_types}
            )]
        
        return [ValidationResult(
            is_valid=True,
            severity=ValidationSeverity.INFO,
            message="File type validation passed",
            details={"filename": filename}
        )]

class ContentQualityValidator(IValidator):
    """Validate extracted content quality"""
    
    def __init__(self, min_length: int = 100):
        super().__init__()
        self.min_length = min_length
    
    def validate(self, data: Dict[str, Any]) -> List[ValidationResult]:
        content = data.get("full_text", "")
        content_length = len(content.strip())
        
        results = []
        
        # Check minimum length
        if content_length < self.min_length:
            results.append(ValidationResult(
                is_valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Content too short: {content_length} chars (minimum {self.min_length})",
                details={"content_length": content_length, "min_length": self.min_length}
            ))
        
        # Check for garbled text
        if content_length > 0:
            non_ascii_ratio = sum(1 for c in content if ord(c) > 127) / content_length
            if non_ascii_ratio > 0.3:
                results.append(ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.WARNING,
                    message=f"High non-ASCII character ratio: {non_ascii_ratio:.2%}",
                    details={"non_ascii_ratio": non_ascii_ratio}
                ))
        
        if not results:
            results.append(ValidationResult(
                is_valid=True,
                severity=ValidationSeverity.INFO,
                message="Content quality validation passed",
                details={"content_length": content_length}
            ))
        
        return results

class ContractStructureValidator(IValidator):
    """Validate contract structure and required fields"""
    
    def validate(self, data: Dict[str, Any]) -> List[ValidationResult]:
        results = []
        required_fields = ["contract_type", "summary", "parties"]
        
        for field in required_fields:
            if not data.get(field):
                results.append(ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.WARNING,
                    message=f"Missing or empty required field: {field}",
                    details={"field": field}
                ))
        
        # Validate parties structure
        parties = data.get("parties", [])
        if not isinstance(parties, list) or len(parties) == 0:
            results.append(ValidationResult(
                is_valid=False,
                severity=ValidationSeverity.WARNING,
                message="No parties identified in contract",
                details={"parties_count": len(parties) if isinstance(parties, list) else 0}
            ))
        
        # Validate dates
        effective_date = data.get("effective_date")
        end_date = data.get("end_date")
        
        if effective_date and end_date:
            try:
                from datetime import datetime
                eff = datetime.fromisoformat(effective_date.replace('Z', '+00:00'))
                end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                
                if end < eff:
                    results.append(ValidationResult(
                        is_valid=False,
                        severity=ValidationSeverity.ERROR,
                        message="End date is before effective date",
                        details={"effective_date": effective_date, "end_date": end_date}
                    ))
            except Exception as e:
                results.append(ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.WARNING,
                    message=f"Invalid date format: {str(e)}",
                    details={"error": str(e)}
                ))
        
        if not results:
            results.append(ValidationResult(
                is_valid=True,
                severity=ValidationSeverity.INFO,
                message="Contract structure validation passed",
                details={"validated_fields": required_fields}
            ))
        
        return results

class SecurityValidator(IValidator):
    """
    Validate security concerns in content. Reuses PIIEngine (backend/
    governance/pii_engine.py) - the same detection logic the chat path's
    PIIValidator/OutputGuard already use - as the single source of truth
    for what counts as PII, rather than maintaining a second, independently
    reimplemented pattern set here.
    """

    def validate(self, data: Dict[str, Any]) -> List[ValidationResult]:
        results = []
        content = data.get("full_text", "")

        matches = PIIEngine.detect(content)
        counts_by_type = defaultdict(int)
        for pii_type, _ in matches:
            counts_by_type[pii_type] += 1

        for pii_type, count in counts_by_type.items():
            results.append(ValidationResult(
                # is_valid=False (not True) so a PII hit is actually visible
                # to ContentValidationService's has_warnings/has_errors
                # aggregation - previously this was always True regardless
                # of what was found, making every PII hit silently invisible
                # to any caller. WARNING (not ERROR) so it's surfaced, not
                # blocking - redaction happens at the storage layer instead
                # (see Neo4jContractRepository.store_contract).
                is_valid=False,
                severity=ValidationSeverity.WARNING,
                message=f"Potential {pii_type} detected in content",
                details={"pii_type": pii_type, "count": count}
            ))

        if not results:
            results.append(ValidationResult(
                is_valid=True,
                severity=ValidationSeverity.INFO,
                message="Security validation passed",
                details={}
            ))

        return results

class ContentValidationService:
    """Facade for content validation - Facade Pattern"""
    
    def __init__(self):
        # Build validation chain
        self.validator_chain = self._build_validation_chain()
    
    def _build_validation_chain(self) -> IValidator:
        """Build chain of validators"""
        file_size = FileSizeValidator(max_size_mb=50)
        file_type = FileTypeValidator(allowed_types=[".pdf"])
        content_quality = ContentQualityValidator(min_length=100)
        structure = ContractStructureValidator()
        security = SecurityValidator()
        
        # Chain validators
        file_size.set_next(file_type).set_next(content_quality).set_next(structure).set_next(security)
        
        return file_size
    
    def _build_file_validation_chain(self) -> IValidator:
        """Build chain for file upload validation (no contract structure)"""
        file_size = FileSizeValidator(max_size_mb=50)
        file_type = FileTypeValidator(allowed_types=[".pdf"])
        
        # Only chain file-level validators
        file_size.set_next(file_type)
        
        return file_size
    
    def validate_file_upload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate file upload data (filename, size only)"""
        try:
            file_validator = self._build_file_validation_chain()
            results = file_validator.validate_chain(data)
            
            # Aggregate results
            has_errors = any(not r.is_valid and r.severity == ValidationSeverity.ERROR for r in results)
            has_warnings = any(not r.is_valid and r.severity == ValidationSeverity.WARNING for r in results)
            has_critical = any(not r.is_valid and r.severity == ValidationSeverity.CRITICAL for r in results)
            
            return {
                "is_valid": not (has_errors or has_critical),
                "has_warnings": has_warnings,
                "has_errors": has_errors,
                "has_critical": has_critical,
                "results": [r.to_dict() for r in results],
                "summary": {
                    "total_checks": len(results),
                    "passed": sum(1 for r in results if r.is_valid),
                    "failed": sum(1 for r in results if not r.is_valid),
                    "errors": sum(1 for r in results if r.severity == ValidationSeverity.ERROR),
                    "warnings": sum(1 for r in results if r.severity == ValidationSeverity.WARNING)
                }
            }
            
        except Exception as e:
            logger.error(f"File validation failed: {e}")
            return {
                "is_valid": False,
                "has_errors": True,
                "has_critical": True,
                "results": [{
                    "is_valid": False,
                    "severity": "critical",
                    "message": f"File validation error: {str(e)}",
                    "details": {}
                }],
                "summary": {"total_checks": 0, "passed": 0, "failed": 1, "errors": 1, "warnings": 0}
            }
    
    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate data and return comprehensive results"""
        try:
            results = self.validator_chain.validate_chain(data)
            
            # Aggregate results
            has_errors = any(not r.is_valid and r.severity == ValidationSeverity.ERROR for r in results)
            has_warnings = any(not r.is_valid and r.severity == ValidationSeverity.WARNING for r in results)
            has_critical = any(not r.is_valid and r.severity == ValidationSeverity.CRITICAL for r in results)
            
            return {
                "is_valid": not (has_errors or has_critical),
                "has_warnings": has_warnings,
                "has_errors": has_errors,
                "has_critical": has_critical,
                "results": [r.to_dict() for r in results],
                "summary": {
                    "total_checks": len(results),
                    "passed": sum(1 for r in results if r.is_valid),
                    "failed": sum(1 for r in results if not r.is_valid),
                    "errors": sum(1 for r in results if r.severity == ValidationSeverity.ERROR),
                    "warnings": sum(1 for r in results if r.severity == ValidationSeverity.WARNING)
                }
            }
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            return {
                "is_valid": False,
                "has_errors": True,
                "has_critical": True,
                "results": [{
                    "is_valid": False,
                    "severity": "critical",
                    "message": f"Validation system error: {str(e)}",
                    "details": {}
                }],
                "summary": {"total_checks": 0, "passed": 0, "failed": 1, "errors": 1, "warnings": 0}
            }
