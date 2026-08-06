import json
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST

from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, AIMessageChunk
from backend.llm_manager import LLMManager
from backend.api.document_upload import router as document_router
from backend.api.contract_intelligence import router as intelligence_router
from backend.api.routes.debug import create_debug_router
from backend.shared.utils.route_utils import is_development, is_production, conditionally_include_router
from backend.api.enhanced_contract_search import router as enhanced_search_router
from backend.api.enhanced_document_upload import router as enhanced_upload_router
from backend.agents.agent_workflow_tracker import get_current_workflow_status
from backend.shared.middleware.tracing import TracingMiddleware
from backend.shared.middleware.metrics import PrometheusMiddleware
from backend.shared.middleware.security_headers import SecurityHeadersMiddleware
from backend.shared.middleware.rate_limit import limiter
from backend.shared.monitoring.prometheus_metrics import render_metrics
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from backend.shared.utils.logger import get_logger, correlation_id_var
from backend.governance.prompt_guard import PromptGuard
from backend.governance.output_guard import OutputGuard
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType

logger = get_logger(__name__)

import os
from openinference.instrumentation.langchain import LangChainInstrumentor

load_dotenv()

# Initialize Phoenix tracing (OpenTelemetry)
phoenix_endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
try:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=phoenix_endpoint)))
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
except Exception as e:
    logger.warning(f"Failed to initialize OpenTelemetry tracing: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - Initialize once.
    #
    # Hard-fail before accepting any traffic if running in production
    # without real secrets (audit finding #4) - both no-op outside
    # production, so this has no effect in dev/tests.
    from backend.governance.auth import validate_production_secret
    from backend.infrastructure.encryption import validate_production_key
    validate_production_secret()
    validate_production_key()

    app.state.llm_manager = LLMManager()
    yield
    # Shutdown - cleanup if needed

app = FastAPI(lifespan=lifespan)

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager


# API-level rate limiting (audit finding #16), scoped via @limiter.limit(...)
# to the two unauthenticated auth routes specifically (backend/api/auth_api.py) -
# this wiring (state/exception handler/middleware) is the standard slowapi
# setup, required regardless of which routes actually carry a @limiter.limit.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(TracingMiddleware)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


def _get_cors_origins() -> list:
    """
    Production-readiness audit finding #2 (was: allow_origins=["*"] with
    allow_credentials=True - Starlette's CORSMiddleware reflects the
    actual request Origin back in that combination, since it can't
    literally emit "*" alongside credentials per spec - meaning any
    origin, not just the real frontend, could make credentialed calls).

    Dev stays permissive (matches local-dev convenience: docker-compose's
    `ui` service, arbitrary local ports, etc.). Production requires an
    explicit, comma-separated allow-list via CORS_ALLOWED_ORIGINS - fails
    closed (empty list, nothing allowed) rather than open if unset, same
    "fail closed, not fail open" principle as the debug-route fix.
    """
    if not is_production():
        return ["*"]

    origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if not origins:
        logger.warning(
            "CORS_ALLOWED_ORIGINS is not set in production - no cross-origin "
            "requests will be allowed until it is. Set a comma-separated list "
            "of real frontend origins, e.g. https://app.example.com"
        )
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_get_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers based on environment
app.include_router(document_router)
app.include_router(intelligence_router)
app.include_router(enhanced_search_router, prefix="/api")
app.include_router(enhanced_upload_router)

# Feedback API (Phase 2)
from backend.api.feedback_api import router as feedback_router
app.include_router(feedback_router)

# Monitoring API (Phase 3)
from backend.api.monitoring_api import router as monitoring_router
app.include_router(monitoring_router)

# Audit API (Production)
from backend.api.audit_api import router as audit_router
app.include_router(audit_router)

# Auth API (JWT token issuance, org invites, MFA)
from backend.api.auth_api import router as auth_router
app.include_router(auth_router)

# Google OIDC SSO (org invites/SSO/MFA design report, this engagement)
from backend.api.sso_api import router as sso_router
app.include_router(sso_router)

# Policy Management API
from backend.api.policy_api import router as policy_router
app.include_router(policy_router)

# Supervisor Agent API - real rebuild, not the deleted dead-code path
# (docs/CAPSTONE_SUMMARY.md §8)
from backend.api.supervisor_api import router as supervisor_router
app.include_router(supervisor_router)

# Debug routes (development only)
debug_router = create_debug_router()
conditionally_include_router(app, debug_router, is_development())

@app.get("/api/workflow/status", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_workflow_status():
    """Get current multi-agent workflow status for executive dashboard"""
    return get_current_workflow_status()

@app.get("/api/planning/status", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_planning_status():
    """Get autonomous planning agent status"""
    from backend.agents.planning.planning_agent import PlanningAgentFactory
    
    planning_agent = PlanningAgentFactory.create_planning_agent()
    return {
        "agent_type": "Autonomous Planning & Reasoning Agent",
        "capabilities": [
            "Query Analysis & Decomposition",
            "Execution Plan Generation", 
            "Self-Reflection & Validation",
            "Adaptive Strategy Selection",
            "Performance Learning"
        ],
        "available_strategies": ["simple", "complex", "risk_focused", "compliance_focused"],
        "execution_history_count": len(planning_agent.execution_history)
    }


@app.get("/")
async def root():
    return {"status": "OK"}


@app.get("/metrics")
async def metrics():
    """Prometheus-compatible scrape endpoint (audit finding #10) - request
    counts/latency by route, LLM cost/token counters (finding #1, Redis-
    backed so this reflects real spend from both the backend and worker
    containers), and Celery task-state counts. Deliberately unauthenticated,
    matching /api/monitoring/health and standard Prometheus/Kubernetes
    convention - scrapers generally don't carry this app's JWT bearer
    tokens, and this endpoint is expected to sit behind network-level
    restriction (not publicly exposed) rather than app-level auth, same as
    /api/monitoring/health.
    """
    return Response(content=render_metrics(), media_type=CONTENT_TYPE_LATEST)


class RunPayload(BaseModel):
    model: str
    prompt: str
    history: str

def rebuild_history(history):
    history = json.loads(history)

    type_to_class = {
        "human": HumanMessage,
        "tool": ToolMessage,
        "ai": AIMessage
    }

    messages = []
    for item_json_str in history:
        item = json.loads(item_json_str)
        item_class = type_to_class.get(item["type"])
        if item_class:
            # use pydantic BaseClass method to rebuild message model from json string dumped by model_dump_json
            messages.append(item_class.model_validate_json(item_json_str))

    return messages


async def runner(model: str, prompt: str, history: str, llm_mgr: LLMManager, tenant_id: str, user_role: str = "unknown"):
    logger.info(f"Processing LLM request for model '{model}' for user_role '{user_role}'")
    
    # Initialize AuditLogger and AgentAuditService for Guard persistence
    from backend.infrastructure.agent_audit_service import AgentAuditService
    
    audit_logger = AuditLogger()
    agent_audit = AgentAuditService(audit_logger)
    session_id = correlation_id_var.get() or "unknown_session"
    context_metadata = {"user_role": user_role}
    
    # 0. Log User Interaction
    agent_audit.log_user_interaction(user_id="user", prompt=prompt, session_id=session_id)

    # 1. Prompt Guard Pre-Check
    guard = PromptGuard(audit_logger=audit_logger)
    guard_result = guard.validate(prompt, context_metadata=context_metadata)
    
    # Log Prompt Guard Check
    agent_audit.log_guard_check(
        guard_name="Prompt Guard",
        is_safe=guard_result.is_safe,
        violation_type=guard_result.violation_type,
        session_id=session_id
    )

    if not guard_result.is_safe:
        logger.error(f"Prompt blocked by Guard: {guard_result.violation_type}")
        yield f"data: {json.dumps({'content': guard_result.message, 'type': 'error'})}\n\n"
        yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"
        return

    # history comes in from FE as stringified list of dumped model messages
    if history != "[]":
        previous_messages = rebuild_history(history)
    else:
        previous_messages = []

    prompt_message = HumanMessage(content=prompt)
    input_messages = [*previous_messages, prompt_message]
    
    corr_id = correlation_id_var.get()
    run_tags = [f"correlation_id:{corr_id}"] if corr_id else []
    
    # tenant_id travels via config["configurable"], not tool-call args - see
    # contract_chat_agent.py's execute_tools, which reads it from here and
    # injects it into the tenant-scoped tools' args itself. The LLM never
    # sees or supplies tenant_id at all (removed from both tools' schemas),
    # so there is no path for it to guess/fabricate a value that could
    # reach another tenant's data - the authenticated JWT's tenant_id
    # (identity.tenant_id, resolved server-side in the /api/run/ route) is
    # the only source, matching every other tenant-scoped operation in
    # this system.
    messages = llm_mgr.get_model_by_name(model).astream(
        input={"messages": input_messages},
        config={"tags": run_tags, "configurable": {"tenant_id": tenant_id}},
        stream_mode=["messages", "updates"]
    )

    # Context management
    context = json.loads(history)
    context.append(prompt_message.model_dump_json())
    
    # Buffer for post-check
    ai_full_content = ""

    async for message in messages:
        if message[0] == "messages":
            chunk = message[1]

            # output tool call section type
            if hasattr(chunk[0], "tool_calls") and len(chunk[0].tool_calls) > 0:
                for tool in chunk[0].tool_calls:
                    if tool.get('name'):
                        tool_calls_content = json.dumps(tool)
                        yield f"data: {json.dumps({'content': tool_calls_content, 'type': 'tool_call'})}\n\n"

            if isinstance(chunk[0], ToolMessage):
                yield f"data: {json.dumps({'content': chunk[0].content, 'type': 'tool_message'})}\n\n"
            if isinstance(chunk[0], AIMessageChunk):
                yield f"data: {json.dumps({'content': chunk[0].content, 'type': 'ai_message'})}\n\n"
            if isinstance(chunk[0], HumanMessage):
                yield f"data: {json.dumps({'content': chunk[0].content, 'type': 'user_message'})}\n\n"

        if message[0] == "updates":
            # use pydantic BaseClass method model_dump_json to dump message model to be stringified into history
            if "assistant" in message[1]:
                for history_message in message[1]["assistant"]["messages"]:
                    context.append(history_message.model_dump_json())
            elif "tools" in message[1]:
                for tool_message in message[1]["tools"]["messages"]:
                    if hasattr(tool_message, 'model_dump_json'):
                        context.append(tool_message.model_dump_json())
                    else:
                        context.append(json.dumps(tool_message))
        
        # Capture AI content for post-check
        if message[0] == "messages":
            chunk = message[1][0]
            if isinstance(chunk, AIMessageChunk):
                ai_full_content += chunk.content

    # 2. Llama Guard Post-Check
    # Extract source context from tool results for hallucination check
    tool_contents = []
    for msg_str in context:
        try:
            msg = json.loads(msg_str)
            if msg.get("type") == "tool":
                tool_contents.append(msg.get("content", ""))
        except Exception:
            pass
    
    if tool_contents:
        context_metadata["source_text"] = "\n---\n".join(tool_contents)

    output_guard = OutputGuard(audit_logger=audit_logger)
    post_check_result = output_guard.validate(ai_full_content, context_metadata=context_metadata)
    
    # Log Output Guard Check
    agent_audit.log_guard_check(
        guard_name="Output Guard",
        is_safe=post_check_result.is_safe,
        violation_type=post_check_result.violation_type,
        session_id=session_id
    )

    if not post_check_result.is_safe:
        logger.error(f"Output blocked by Llama Guard: {post_check_result.violation_type}")
        # Notify user about the violation even if part of the content was streamed
        yield f"data: {json.dumps({'content': ' [CONTENT REMOVED DUE TO SAFETY POLICY] ' + post_check_result.message, 'type': 'error'})}\n\n"
    else:
        # If PII was redacted, we should update the context
        redacted_content = post_check_result.metadata.get("redacted_content")
        if redacted_content and redacted_content != ai_full_content:
            logger.info("PII redaction applied to AI output")
            # Log PII redaction to audit trail
            audit_logger.log_event(
                event_type=AuditEventType.SECURITY_VIOLATION,
                resource_id=session_id,
                action="pii_redaction",
                metadata={"status": "redacted"}
            )
            # Update the last message in context with the redacted version
            for i in range(len(context) - 1, -1, -1):
                msg_data = json.loads(context[i])
                if msg_data.get("type") == "ai":
                    msg_data["content"] = redacted_content
                    context[i] = json.dumps(msg_data)
                    break

    yield f"data: {json.dumps({'content': context, 'type': 'history'})}\n\n"
    yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"


@app.post("/api/run/")
async def run(
    payload: RunPayload,
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    return StreamingResponse(
        runner(
            model=payload.model,
            prompt=payload.prompt,
            history=payload.history,
            llm_mgr=llm_mgr,
            tenant_id=identity.tenant_id,
            user_role=identity.role,
        ),
        media_type="text/event-stream",
    )