"""
Test Suite for Audit Logging, Content Validation, and Error Tracking
"""

import pytest
from collections import Counter
from unittest.mock import patch
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType
from backend.infrastructure.content_validator import ContentValidationService, ValidationSeverity
from backend.infrastructure.error_tracker import ErrorTracker, ErrorCategory, ErrorSeverity, ErrorContext, error_tracking_context


class FakeGraph:
    """
    Minimal in-memory stand-in for Neo4jGraph, understanding just the
    MERGE/SET/MATCH/RETURN shapes AuditLogger and ErrorTracker use.

    test_audit_trail_retrieval and test_error_tracker_statistics previously
    passed or failed depending on whichever Neo4jGraph mock happened to
    already be cached in sys.modules by an unrelated test file collected
    earlier in the same pytest session - typically a bare MagicMock(),
    whose default __iter__ silently yields nothing. That meant these two
    tests were not reliably exercising get_audit_trail/get_error_statistics's
    real retrieval logic at all (log_event/track_error still "succeeded"
    against a MagicMock, since subscripting one just returns another
    MagicMock). This fake makes both tests deterministic and actually
    exercise that logic, regardless of what else is cached elsewhere in the
    session.
    """

    def __init__(self):
        self.audit_logs = []
        self.error_logs = []

    def query(self, cypher, params=None):
        params = params or {}

        if "MERGE (a:AuditLog" in cypher:
            self.audit_logs.append(dict(params))
            return [{"audit_id": params["audit_id"]}]

        if "MATCH (a:AuditLog" in cypher:
            matches = [
                r for r in self.audit_logs
                if r["resource_id"] == params["resource_id"] and r["tenant_id"] == params["tenant_id"]
            ]
            matches = matches[-params.get("limit", 100):][::-1]
            return [
                {
                    "audit_id": r["audit_id"], "event_type": r["event_type"], "action": r["action"],
                    "user_id": r["user_id"], "status": r["status"], "timestamp": r["audit_id"],
                    "metadata": r["metadata"],
                }
                for r in matches
            ]

        if "MERGE (e:ErrorLog" in cypher:
            self.error_logs.append(dict(params))
            return [{"error_id": params["error_id"]}]

        if "MATCH (e:ErrorLog" in cypher and "count(*)" in cypher:
            counts = Counter(
                (r["category"], r["severity"])
                for r in self.error_logs if r["tenant_id"] == params["tenant_id"]
            )
            return [{"category": cat, "severity": sev, "count": n} for (cat, sev), n in counts.items()]

        if "MATCH (e:ErrorLog" in cypher:
            return [
                r for r in reversed(self.error_logs) if r["tenant_id"] == params["tenant_id"]
            ][:params.get("limit", 50)]

        return []


def _with_fake_graph(obj):
    """Attach a fresh FakeGraph to an AuditLogger/ErrorTracker instance,
    overriding whatever its constructor happened to wire up."""
    obj.repository.graph = FakeGraph()
    return obj

def test_audit_logger_basic():
    """Test basic audit logging functionality"""
    audit_logger = AuditLogger()
    
    audit_id = audit_logger.log_event(
        event_type=AuditEventType.DOCUMENT_UPLOAD,
        resource_id="test_contract_123",
        action="test_upload",
        tenant_id="tenant_test",
        status="success",
        metadata={"test": "data"}
    )
    
    assert audit_id != ""
    print(f"✅ Audit logged: {audit_id}")

def test_audit_trail_retrieval():
    """Test audit trail retrieval"""
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        audit_logger = _with_fake_graph(AuditLogger())

    # Log multiple events
    for i in range(3):
        audit_logger.log_event(
            event_type=AuditEventType.DOCUMENT_ACCESS,
            resource_id="test_contract_456",
            action=f"access_{i}",
            tenant_id="tenant_test",
            status="success"
        )
    
    # Retrieve trail
    trail = audit_logger.get_audit_trail("test_contract_456", "tenant_test", limit=10)
    
    assert len(trail) >= 3
    print(f"✅ Retrieved {len(trail)} audit events")

def test_content_validator_file_size():
    """Test file size validation"""
    validator = ContentValidationService()
    
    # Valid file size with full_text to pass content quality check
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024 * 1024,  # 1MB
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == True
    print(f"✅ File size validation passed: {result['summary']}")
    
    # Invalid file size
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 100 * 1024 * 1024,  # 100MB
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == False
    assert result["has_errors"] == True
    print(f"✅ File size validation correctly failed: {result['summary']}")

def test_content_validator_file_type():
    """Test file type validation"""
    validator = ContentValidationService()
    
    # Valid file type
    result = validator.validate({
        "filename": "contract.pdf",
        "file_size": 1024,
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == True
    print(f"✅ File type validation passed")
    
    # Invalid file type
    result = validator.validate({
        "filename": "contract.docx",
        "file_size": 1024,
        "full_text": "Valid contract content " * 20
    })
    
    assert result["is_valid"] == False
    print(f"✅ File type validation correctly failed")

def test_content_validator_quality():
    """Test content quality validation"""
    validator = ContentValidationService()
    
    # Valid content
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "This is a valid contract with sufficient content. " * 10
    })
    
    assert result["is_valid"] == True
    print(f"✅ Content quality validation passed")
    
    # Invalid content (too short)
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Short"
    })
    
    assert result["is_valid"] == False
    print(f"✅ Content quality validation correctly failed for short content")

def test_content_validator_structure():
    """Test contract structure validation"""
    validator = ContentValidationService()
    
    # Valid structure
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Valid content " * 20,
        "contract_type": "Service Agreement",
        "summary": "Test contract summary",
        "parties": [{"name": "Party A", "role": "Provider"}]
    })
    
    assert result["is_valid"] == True
    print(f"✅ Contract structure validation passed")
    
    # Missing required fields
    result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Valid content " * 20
    })
    
    assert result["has_warnings"] == True
    print(f"✅ Contract structure validation detected missing fields")

def test_error_tracker_basic():
    """Test basic error tracking"""
    error_tracker = ErrorTracker()
    
    context = ErrorContext(
        operation="test_operation",
        resource_id="test_resource",
        tenant_id="tenant_test",
        metadata={"test": "data"}
    )
    
    try:
        raise ValueError("Test error")
    except Exception as e:
        error_id = error_tracker.track_error(
            error=e,
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.MEDIUM,
            context=context
        )
        
        assert error_id != ""
        print(f"✅ Error tracked: {error_id}")

def test_error_tracker_statistics():
    """Test error statistics retrieval"""
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        error_tracker = _with_fake_graph(ErrorTracker())

    # Track multiple errors
    for i in range(3):
        context = ErrorContext(operation=f"test_op_{i}", tenant_id="tenant_test")
        try:
            raise RuntimeError(f"Test error {i}")
        except Exception as e:
            error_tracker.track_error(
                error=e,
                category=ErrorCategory.PROCESSING_ERROR,
                severity=ErrorSeverity.HIGH,
                context=context
            )
    
    # Get statistics
    stats = error_tracker.get_error_statistics("tenant_test", hours=24)
    
    assert stats["total_errors"] >= 3
    print(f"✅ Error statistics: {stats}")

def test_error_tracking_context_manager():
    """Test error tracking context manager"""
    
    # Test successful operation
    with error_tracking_context(
        operation="test_success",
        category=ErrorCategory.PROCESSING_ERROR,
        resource_id="test_123",
        tenant_id="tenant_test",
    ) as ctx:
        result = "success"
    
    assert len(ctx.errors) == 0
    print(f"✅ Context manager tracked successful operation")
    
    # Test failed operation (suppressed)
    with error_tracking_context(
        operation="test_failure",
        category=ErrorCategory.PROCESSING_ERROR,
        resource_id="test_456",
        tenant_id="tenant_test",
        raise_on_error=False
    ) as ctx:
        raise ValueError("Test error")
    
    assert len(ctx.errors) == 1
    print(f"✅ Context manager tracked failed operation: {ctx.errors}")

def test_integration_validation_with_audit():
    """Test integration of validation with audit logging"""
    validator = ContentValidationService()
    audit_logger = AuditLogger()
    
    # Validate content
    validation_result = validator.validate({
        "filename": "test.pdf",
        "file_size": 1024,
        "full_text": "Test content " * 20
    })
    
    # Log validation result
    if not validation_result["is_valid"]:
        audit_id = audit_logger.log_event(
            event_type=AuditEventType.VALIDATION_FAILURE,
            resource_id="test.pdf",
            action="content_validation",
            tenant_id="tenant_test",
            status="failure",
            error_details=str(validation_result)
        )
        assert audit_id != ""
    
    print(f"✅ Validation and audit integration working")

if __name__ == "__main__":
    print("=== Testing Audit Logging ===")
    test_audit_logger_basic()
    test_audit_trail_retrieval()
    
    print("\n=== Testing Content Validation ===")
    test_content_validator_file_size()
    test_content_validator_file_type()
    test_content_validator_quality()
    test_content_validator_structure()
    
    print("\n=== Testing Error Tracking ===")
    test_error_tracker_basic()
    test_error_tracker_statistics()
    test_error_tracking_context_manager()
    
    print("\n=== Testing Integration ===")
    test_integration_validation_with_audit()
    
    print("\n✅ All tests passed!")
