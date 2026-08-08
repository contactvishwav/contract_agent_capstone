import logging
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Dict, Any, Optional
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType, AuditScope

# Context variable to store trace_id for the current task/request
trace_id_var: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)

class MCPLogger:
    """
    Centralized logging framework for MCP layer with request tracing.
    Follows SOLID principles by separating tracing logic from business logic.
    """
    
    def __init__(self, name: str = "mcp_server"):
        self.logger = logging.getLogger(name)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - [TraceID: %(trace_id)s] - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
            
        self.audit_logger = AuditLogger()

    def _get_trace_id(self) -> str:
        tid = trace_id_var.get()
        if tid is None:
            tid = str(uuid.uuid4())
            trace_id_var.set(tid)
        return tid

    def info(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        tid = self._get_trace_id()
        self.logger.info(message, extra={"trace_id": tid})
        if metadata:
            self.logger.info(f"Metadata: {metadata}", extra={"trace_id": tid})

    def error(
        self,
        message: str,
        error: Exception,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ):
        tid = self._get_trace_id()
        self.logger.error(
            f"{message}: {type(error).__name__}",
            extra={"trace_id": tid},
            # exc_info would append the exception's raw message. Tool and
            # provider errors may contain prompts, document text, or secrets.
            exc_info=False,
        )
        
        # Log to AuditLogger for persistence in Neo4j
        self.audit_logger.log_event(
            event_type=AuditEventType.PROCESSING_ERROR,
            resource_id="mcp_server",
            action=message,
            status="error",
            error_details=type(error).__name__,
            tenant_id=tenant_id,
            scope=AuditScope.TENANT if tenant_id else AuditScope.SYSTEM,
            metadata={**(metadata or {}), "trace_id": tid}
        )

    def log_tool_execution(self, tool_name: str, tenant_id: str, status: str = "success", metadata: Optional[Dict[str, Any]] = None):
        tid = self._get_trace_id()
        self.info(f"Executing tool {tool_name} for tenant {tenant_id}", metadata=metadata)
        
        # Persistent audit trail in Neo4j
        self.audit_logger.log_event(
            event_type=AuditEventType.ANALYSIS_REQUEST,
            resource_id=tool_name,
            action="execute_mcp_tool",
            tenant_id=tenant_id,
            status=status,
            metadata={**(metadata or {}), "trace_id": tid}
        )

def get_mcp_logger(name: str = "mcp_server") -> MCPLogger:
    return MCPLogger(name)
