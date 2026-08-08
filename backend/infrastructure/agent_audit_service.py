from typing import Dict, Any, Optional
from .audit_logger import AuditLogger, AuditEventType
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

class AgentAuditService:
    """
    Service for granular agentic auditing.
    Provides methods to log specific agent lifecycle events.
    """
    
    def __init__(
        self,
        audit_logger: Optional[AuditLogger] = None,
        tenant_id: Optional[str] = None,
        user_id: str = "authenticated_user",
        correlation_id: Optional[str] = None,
    ):
        self.audit_logger = audit_logger or AuditLogger()
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.correlation_id = correlation_id

    def log_user_interaction(self, user_id: str, prompt: str, session_id: str, metadata: Optional[Dict[str, Any]] = None):
        """Log the initial user prompt"""
        self.audit_logger.log_event(
            event_type=AuditEventType.USER_INTERACTION,
            resource_id=session_id,
            action="receive_prompt",
            user_id=user_id or self.user_id,
            tenant_id=self.tenant_id,
            metadata={
                "prompt_length": len(prompt),
                "correlation_id": self.correlation_id,
                **(metadata or {})
            }
        )

    def log_tool_execution(self, tool_name: str, args: Dict[str, Any], result: str, session_id: str, status: str = "success"):
        """Log a tool execution and its result"""
        self.audit_logger.log_event(
            event_type=AuditEventType.AGENT_TOOL_CALL,
            resource_id=session_id,
            action=f"execute_{tool_name}",
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            status=status,
            metadata={
                "tool_name": tool_name,
                "argument_names": sorted(args.keys()),
                "result_length": len(str(result)),
                "correlation_id": self.correlation_id,
            }
        )

    def log_model_decision(self, rationale: str, session_id: str, metadata: Optional[Dict[str, Any]] = None):
        """Log an LLM thought or decision step"""
        self.audit_logger.log_event(
            event_type=AuditEventType.AGENT_THOUGHT,
            resource_id=session_id,
            action="llm_decision",
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            metadata={
                "response_length": len(rationale),
                "correlation_id": self.correlation_id,
                **(metadata or {})
            }
        )

    def log_guard_check(
        self,
        guard_name: str,
        is_safe: bool,
        violation_type: Optional[str],
        session_id: str,
        validation_status: Optional[str] = None,
        model: Optional[str] = None,
        chat_session_id: Optional[str] = None,
        reason_category: Optional[str] = None,
    ):
        """Log a governance guard check (Prompt/Output Guard)"""
        self.audit_logger.log_event(
            event_type=AuditEventType.MODEL_GUARD_CHECK,
            resource_id=session_id,
            action=f"check_{guard_name.lower().replace(' ', '_')}",
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            status=validation_status or ("success" if is_safe else "violation"),
            metadata={
                "guard": guard_name,
                "is_safe": is_safe,
                "violation_type": violation_type,
                "validation_status": validation_status,
                "model": model,
                "chat_session_id": chat_session_id,
                "reason_category": reason_category,
                "correlation_id": self.correlation_id,
            }
        )
