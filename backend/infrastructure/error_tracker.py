"""
Centralized Error Tracking System
Context Manager + Observer Pattern for comprehensive error handling
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
from contextlib import contextmanager
import traceback
import json

from backend.shared.utils.logger import get_logger
from backend.shared.utils.utils import serialize_neo4j_datetime
logger = get_logger(__name__)

class ErrorCategory(Enum):
    """Error categories for classification"""
    VALIDATION_ERROR = "validation_error"
    PROCESSING_ERROR = "processing_error"
    DATABASE_ERROR = "database_error"
    NETWORK_ERROR = "network_error"
    AI_MODEL_ERROR = "ai_model_error"
    FILE_ERROR = "file_error"
    AUTHENTICATION_ERROR = "authentication_error"
    BUSINESS_LOGIC_ERROR = "business_logic_error"
    SYSTEM_ERROR = "system_error"

class ErrorSeverity(Enum):
    """Error severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorContext:
    """Error context with rich metadata"""
    def __init__(
        self,
        operation: str,
        resource_id: Optional[str] = None,
        user_id: Optional[str] = "system",
        tenant_id: Optional[str] = None,
        system_event: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.operation = operation
        self.resource_id = resource_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.scope = "system" if system_event else "tenant"
        self.metadata = metadata or {}
        self.start_time = datetime.utcnow()
        self.errors: List[Dict[str, Any]] = []

class ErrorTracker:
    """Centralized error tracking with Neo4j persistence"""
    
    def __init__(self):
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        self.repository = Neo4jContractRepository()
    
    def track_error(
        self,
        error: Exception,
        category: ErrorCategory,
        severity: ErrorSeverity,
        context: ErrorContext,
        recovery_action: Optional[str] = None
    ) -> str:
        """Track an error without unrestricted exception payload content."""
        if context.scope == "tenant" and not context.tenant_id:
            logger.error("Refusing to persist tenant error without tenant_id")
            return ""
        if context.scope == "system":
            context.tenant_id = None
        try:
            error_id = f"error_{datetime.utcnow().timestamp()}"
            
            error_data = {
                "error_id": error_id,
                "error_type": type(error).__name__,
                "error_message": type(error).__name__,
                "category": category.value,
                "severity": severity.value,
                "operation": context.operation,
                "resource_id": context.resource_id or "unknown",
                "user_id": context.user_id,
                "tenant_id": context.tenant_id,
                "scope": context.scope,
                "timestamp": datetime.utcnow().isoformat(),
                "stack_trace": json.dumps([
                    {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(error.__traceback__)
                ]),
                "metadata": json.dumps(context.metadata),
                "recovery_action": recovery_action
            }
            
            # Store in Neo4j
            query = """
            MERGE (e:ErrorLog {error_id: $error_id})
            SET e.error_type = $error_type,
                e.error_message = $error_message,
                e.category = $category,
                e.severity = $severity,
                e.operation = $operation,
                e.resource_id = $resource_id,
                e.user_id = $user_id,
                e.tenant_id = $tenant_id,
                e.scope = $scope,
                e.timestamp = datetime($timestamp),
                e.stack_trace = $stack_trace,
                e.metadata = $metadata,
                e.recovery_action = $recovery_action
            RETURN e.error_id as error_id
            """
            
            result = self.repository.graph.query(query, error_data)
            
            logger.error(f"Error tracked: {error_id} - {category.value} - {severity.value}")
            logger.error(f"Error details: {error_data['error_message']}")
            
            return result[0]["error_id"] if result else error_id
            
        except Exception as exc:
            logger.error("Failed to track error: %s", type(exc).__name__)
            return ""
    
    def get_error_statistics(self, tenant_id: str, hours: int = 24) -> Dict[str, Any]:
        """Get error statistics for monitoring"""
        try:
            query = """
            MATCH (e:ErrorLog {tenant_id: $tenant_id})
            WHERE e.timestamp > datetime() - duration({hours: $hours})
            RETURN 
                e.category as category,
                e.severity as severity,
                count(*) as count
            ORDER BY count DESC
            """
            
            result = self.repository.graph.query(query, {"hours": hours, "tenant_id": tenant_id})
            
            stats = {
                "total_errors": sum(row["count"] for row in result),
                "by_category": {},
                "by_severity": {}
            }
            
            for row in result:
                category = row["category"]
                severity = row["severity"]
                count = row["count"]
                
                stats["by_category"][category] = stats["by_category"].get(category, 0) + count
                stats["by_severity"][severity] = stats["by_severity"].get(severity, 0) + count
            
            return stats
            
        except Exception as exc:
            logger.error("Failed to get error statistics: %s", type(exc).__name__)
            return {"total_errors": 0, "by_category": {}, "by_severity": {}}
    
    def get_recent_errors(self, tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent errors for debugging"""
        try:
            query = """
            MATCH (e:ErrorLog {tenant_id: $tenant_id})
            RETURN e.error_id as error_id,
                   e.error_type as error_type,
                   e.error_message as error_message,
                   e.category as category,
                   e.severity as severity,
                   e.operation as operation,
                   e.resource_id as resource_id,
                   e.timestamp as timestamp
            ORDER BY e.timestamp DESC
            LIMIT $limit
            """
            
            result = self.repository.graph.query(query, {"limit": limit, "tenant_id": tenant_id})
            errors = []
            for row in result:
                error = dict(row)
                # e.timestamp is stored via datetime($timestamp) (see
                # track_error above), so it comes back as a raw neo4j.time.
                # DateTime object here - same leak class as AuditLogger.
                # get_audit_trail, reachable via GET /api/audit/errors/recent.
                error["timestamp"] = serialize_neo4j_datetime(error.get("timestamp"))
                errors.append(error)
            return errors

        except Exception as exc:
            logger.error("Failed to get recent errors: %s", type(exc).__name__)
            return []

@contextmanager
def error_tracking_context(
    operation: str,
    category: ErrorCategory,
    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    resource_id: Optional[str] = None,
    user_id: Optional[str] = "system",
    tenant_id: Optional[str] = None,
    system_event: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    raise_on_error: bool = True
):
    """Context manager for automatic error tracking"""
    context = ErrorContext(
        operation=operation,
        resource_id=resource_id,
        user_id=user_id,
        tenant_id=tenant_id,
        system_event=system_event,
        metadata=metadata
    )
    
    tracker = ErrorTracker()
    
    try:
        yield context
        
    except Exception as e:
        # Track the error
        error_id = tracker.track_error(
            error=e,
            category=category,
            severity=severity,
            context=context,
            recovery_action=None
        )
        
        # Add error to context
        context.errors.append({
            "error_id": error_id,
            "error_type": type(e).__name__,
            "error_message": str(e)
        })
        
        if raise_on_error:
            raise
        else:
            logger.error(f"Error suppressed: {error_id}")

class ErrorRecoveryStrategy:
    """Strategy pattern for error recovery"""
    
    @staticmethod
    def retry_with_backoff(func, max_retries: int = 3, backoff_factor: float = 2.0):
        """Retry function with exponential backoff"""
        import time
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                
                wait_time = backoff_factor ** attempt
                logger.warning(f"Retry attempt {attempt + 1}/{max_retries} after {wait_time}s")
                time.sleep(wait_time)
    
    @staticmethod
    def fallback_to_default(func, default_value: Any = None):
        """Execute function with fallback to default value"""
        try:
            return func()
        except Exception as e:
            logger.warning(f"Function failed, using default: {e}")
            return default_value
    
    @staticmethod
    def circuit_breaker(func, failure_threshold: int = 5, timeout: int = 60):
        """Circuit breaker pattern for error handling"""
        # Simplified circuit breaker - production would use more sophisticated implementation
        try:
            return func()
        except Exception as e:
            logger.error(f"Circuit breaker triggered: {e}")
            raise
