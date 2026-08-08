import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request, Response, HTTPException
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
from backend.governance.base import GuardResult, GuardStatus
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType
from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
from backend.application.services.chat_citation_service import build_validated_citations
from backend.shared.monitoring.prometheus_metrics import record_output_guard_outcome

logger = get_logger(__name__)


class ChatPersistenceError(RuntimeError):
    """A safe, content-free marker for terminal-message persistence failure."""

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

# Persistent Contract Chat sessions (list/create/detail) - /api/run/ below
# is where messages actually get appended as a conversation happens.
from backend.api.chat_sessions import (
    contract_exists_for_tenant,
    normalize_contract_scope,
    router as chat_sessions_router,
)
app.include_router(chat_sessions_router)

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
    # Optional now, defaulting to "[]": the frontend no longer populates
    # this once every real turn goes through a persistent session_id (see
    # below) - kept for backward compatibility with any caller that still
    # only wants today's ephemeral, client-managed-history behavior.
    history: Optional[str] = "[]"
    # Optional: which contract the user has selected in the Chat UI, if
    # any. Real, confirmed bug this closes: Contract Chat had no way to
    # know which contract "this"/"it" referred to in a question like
    # "Analyze this contract" - there was no field anywhere in this
    # request for it. Threaded into the agent via config["configurable"],
    # the same trust-boundary pattern as tenant_id (see contract_chat_
    # agent.py's execute_tools) - never exposed to the LLM as a tool-call
    # argument it could guess or override.
    contract_id: Optional[str] = None
    # Optional: which persistent chat session (backend/infrastructure/
    # chat_session_repository.py) this message belongs to. When present,
    # runner() loads conversation history from Neo4j (server-authoritative,
    # matching contract_id/tenant_id's trust boundary - can't be spoofed
    # via the client-supplied `history` string either) instead of trusting
    # `history`, and persists every turn as it happens. Ownership (this
    # session belongs to the caller's tenant) is checked in the /api/run/
    # route itself, before streaming starts - see run() below.
    session_id: Optional[str] = None

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


def _messages_from_stored(stored_messages):
    """Rebuilds LangChain history for the LLM from persisted ChatMessage
    rows (Neo4jChatSessionRepository.list_messages), used instead of
    rebuild_history() whenever a session_id is present.

    Deliberately uses only "user_message"/"ai_message" rows, converted to
    plain HumanMessage/AIMessage(content=...) - "tool_call"/"tool_message"
    rows are persisted (for UI replay - see runner() below) but not
    replayed back into the model's own context here. Restoring a session's
    own prior tool-call JSON/results into the model's context would need a
    fully-formed AIMessage(tool_calls=[...]) + matching
    ToolMessage(tool_call_id=...) pair sequence; ChatMessage.tool_call_id
    exists specifically so that's possible as a fast-follow without another
    migration, but isn't done here. Practical effect: a restored session's
    model sees its own prior natural-language answers, not its own prior
    raw tool arguments/results, in later turns of that same session.
    """
    role_to_class = {"user_message": HumanMessage, "ai_message": AIMessage}
    messages = []
    for row in stored_messages:
        message_class = role_to_class.get(row.get("role"))
        if message_class:
            messages.append(message_class(content=row.get("content") or ""))
    return messages


def _normalize_ai_message_content(content):
    """LangChain's AIMessageChunk.content is not consistently shaped -
    real, confirmed bug found live during a full Contract Chat functional
    audit: it's usually a plain str (the normal token-by-token streaming
    case), but for some responses (confirmed: any direct final-text turn
    with no preceding tool call, at least with Gemini) it's a list of
    content-block dicts instead, e.g. [{"type": "text", "text": "...",
    "extras": {"signature": "..."}, "index": 0}] - Gemini's thought-
    signature grounding metadata riding along as a structured block.

    This used to be forwarded to the frontend completely unnormalized.
    message.tsx's default render case does `<Fragment>{content}</Fragment>`
    with no shape check - when content was that list of dicts, React threw
    "Objects are not valid as a React child", and since ChatPage carried no
    ErrorBoundary (also fixed in this pass - see App.tsx), that crash took
    down the entire app with nothing to catch it: a blank white page,
    confirmed live and reproduced.

    Flattened here to plain text before it ever reaches the frontend -
    the wire format Contract Chat's UI has always assumed content to be.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content) if content else ""


def _guard_status(result: GuardResult) -> GuardStatus:
    """Normalize real and legacy/mock GuardResult shapes."""
    status = getattr(result, "status", None)
    if isinstance(status, GuardStatus):
        return status
    if isinstance(status, str):
        try:
            return GuardStatus(status)
        except ValueError:
            pass
    return GuardStatus.PASSED if result.is_safe else GuardStatus.REJECTED


async def _validate_output_guard(
    output_guard: OutputGuard,
    content: str,
    context_metadata: dict,
) -> GuardResult:
    """Run blocking provider validators off-loop with a bounded wait."""
    try:
        timeout_seconds = float(os.getenv("OUTPUT_GUARD_TIMEOUT_SECONDS", "60"))
    except ValueError:
        timeout_seconds = 60.0
    timeout_seconds = max(0.1, timeout_seconds)

    try:
        async_validator = getattr(output_guard, "avalidate", None)
        if async_validator and inspect.iscoroutinefunction(async_validator):
            validation = async_validator(
                content,
                context_metadata=context_metadata,
            )
        else:
            # Compatibility for deterministic legacy/custom validators and
            # existing mocks. Production OutputGuard uses its true async path,
            # so timeout/cancellation cancels provider awaitables instead of
            # leaving a background validation thread running.
            validation = asyncio.to_thread(
                output_guard.validate,
                content,
                context_metadata=context_metadata,
            )
        return await asyncio.wait_for(validation, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.error("Output Guard timed out; content omitted")
        return GuardResult(
            is_safe=False,
            status=GuardStatus.TIMED_OUT,
            violation_type="VALIDATION_TIMEOUT",
            message="Output validation timed out.",
            metadata={"failure_category": "timeout"},
        )
    except Exception as exc:
        # Defense in depth: validators normally convert their own failures to
        # validation_failed.  No unexpected guard exception may become a pass.
        logger.error(
            f"Output Guard infrastructure failure ({type(exc).__name__}); content omitted"
        )
        return GuardResult(
            is_safe=False,
            status=GuardStatus.VALIDATION_FAILED,
            violation_type="VALIDATOR_INFRASTRUCTURE_FAILURE",
            message="Output validation could not be completed.",
            metadata={
                "failure_category": "infrastructure",
                "exception_type": type(exc).__name__,
            },
        )


def _safe_terminal_message(status: GuardStatus, violation_type: Optional[str]) -> str:
    if status == GuardStatus.REJECTED and violation_type == "UNGROUNDED_OUTPUT":
        return "Response withheld because it could not be verified against contract evidence. Please refine your question or retry."
    if status == GuardStatus.REJECTED:
        return "Response withheld by the safety policy. Please revise your request or retry."
    if status == GuardStatus.TIMED_OUT:
        return "Response validation timed out. Please retry."
    if status == GuardStatus.EMPTY:
        return "The assistant returned no response. Please retry."
    return "Response validation failed. Please retry."


async def runner(model: str, prompt: str, history: str, llm_mgr: LLMManager, tenant_id: str, user_role: str = "unknown", user_id: str = "authenticated_user", contract_id: Optional[str] = None, chat_session_id: Optional[str] = None):
    logger.info(f"Processing LLM request for model '{model}' for user_role '{user_role}'")

    # Initialize AuditLogger and AgentAuditService for Guard persistence
    from backend.infrastructure.agent_audit_service import AgentAuditService

    audit_logger = AuditLogger()
    session_id = correlation_id_var.get() or "unknown_session"
    agent_audit = AgentAuditService(
        audit_logger, tenant_id=tenant_id, user_id=user_id, correlation_id=session_id
    )
    context_metadata = {
        "user_role": user_role,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "correlation_id": session_id,
    }

    # Persistent Contract Chat session (backend/infrastructure/
    # chat_session_repository.py) - deliberately a distinct name from the
    # `session_id` local above, which is this request's audit/correlation
    # id, an unrelated concept. Ownership of chat_session_id (belongs to
    # this tenant) is already checked in the /api/run/ route before
    # streaming starts, so no ownership check is repeated here - only a
    # None-safe repository instantiation.
    chat_session_repo = Neo4jChatSessionRepository() if chat_session_id else None
    
    # 0. Log User Interaction
    agent_audit.log_user_interaction(user_id="user", prompt=prompt, session_id=session_id)

    # 1. Prompt Guard Pre-Check
    guard = PromptGuard(audit_logger=audit_logger)
    guard_result = guard.validate(prompt, context_metadata=context_metadata)
    prompt_guard_status = _guard_status(guard_result)
    
    # Log Prompt Guard Check
    agent_audit.log_guard_check(
        guard_name="Prompt Guard",
        is_safe=guard_result.is_safe,
        violation_type=guard_result.violation_type,
        session_id=session_id,
        validation_status=prompt_guard_status.value,
        model=model,
        chat_session_id=chat_session_id,
        reason_category=(getattr(guard_result, "metadata", {}) or {}).get("failure_category"),
    )

    if not guard_result.is_safe:
        logger.error(f"Prompt blocked by Guard: {guard_result.violation_type}")
        if chat_session_repo:
            # Still visible on reopen - a declined prompt shouldn't vanish
            # from a restored session just because it never reached the LLM.
            chat_session_repo.append_message(chat_session_id, tenant_id, role="user_message", content=prompt)
            chat_session_repo.append_message(
                chat_session_id,
                tenant_id,
                role="ai_message",
                content=guard_result.message,
                model=model,
                terminal_status=prompt_guard_status.value,
            )
        yield f"data: {json.dumps({'content': guard_result.message, 'type': 'error', 'status': prompt_guard_status.value})}\n\n"
        yield f"data: {json.dumps({'content': '', 'type': 'end', 'status': prompt_guard_status.value})}\n\n"
        return

    if chat_session_repo:
        # Server-authoritative: history is loaded from Neo4j, not trusted
        # from the client's `history` string, matching contract_id/
        # tenant_id's existing trust boundary. The incoming prompt is
        # persisted immediately, before the LLM is even invoked, so it
        # survives a refresh/navigation-away even if the response itself
        # never completes.
        stored_messages = chat_session_repo.list_messages(chat_session_id, tenant_id)
        previous_messages = _messages_from_stored(stored_messages)
        chat_session_repo.append_message(chat_session_id, tenant_id, role="user_message", content=prompt)
    elif history != "[]":
        # history comes in from FE as stringified list of dumped model messages
        previous_messages = rebuild_history(history)
    else:
        previous_messages = []

    prompt_message = HumanMessage(content=prompt)
    input_messages = [*previous_messages, prompt_message]
    
    corr_id = correlation_id_var.get()
    run_tags = [f"correlation_id:{corr_id}"] if corr_id else []
    
    # tenant_id and correlation_id travel via config["configurable"], not
    # tool-call args - see contract_chat_agent.py's execute_tools, which
    # reads both from here and injects them into the relevant tools' args
    # itself. The LLM never sees or supplies tenant_id at all (removed from
    # every tenant-scoped tool's schema), so there is no path for it to
    # guess/fabricate a value that could reach another tenant's data - the
    # authenticated JWT's tenant_id (identity.tenant_id, resolved
    # server-side in the /api/run/ route) is the only source, matching
    # every other tenant-scoped operation in this system. correlation_id is
    # this request's own id (corr_id above, from TracingMiddleware) - it's
    # not a security boundary, just threaded through the same explicit
    # config path so it reaches the 4 MCP-backed chat tools without relying
    # on a contextvar surviving into whatever thread LangGraph runs
    # execute_tools in.
    messages = llm_mgr.get_model_by_name(model).astream(
        input={"messages": input_messages},
        config={"tags": run_tags, "configurable": {"tenant_id": tenant_id, "correlation_id": corr_id, "contract_id": contract_id}},
        stream_mode=["messages", "updates"]
    )

    # Context management
    context = json.loads(history)
    context.append(prompt_message.model_dump_json())
    
    # Buffer for post-check
    ai_full_content = ""
    tool_call_names = {}
    tool_evidence = []

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
                yield f"data: {json.dumps({'content': _normalize_ai_message_content(chunk[0].content), 'type': 'tool_message'})}\n\n"
            # Assistant content is intentionally buffered until Output Guard
            # passes. Streaming it here would disclose content before the
            # post-check could reject or redact it.
            if isinstance(chunk[0], HumanMessage):
                yield f"data: {json.dumps({'content': chunk[0].content, 'type': 'user_message'})}\n\n"

        if message[0] == "updates":
            # use pydantic BaseClass method model_dump_json to dump message model to be stringified into history
            if "assistant" in message[1]:
                for history_message in message[1]["assistant"]["messages"]:
                    context.append(history_message.model_dump_json())
                    for tc in (getattr(history_message, "tool_calls", None) or []):
                        if tc.get("id"):
                            tool_call_names[tc["id"]] = tc.get("name")
                    if chat_session_repo:
                        # These are the real, final AIMessage.tool_calls -
                        # the authoritative source (not re-parsed SSE
                        # strings), same reason the "messages"-stream yield
                        # above exists separately for the live token
                        # stream. The turn's own natural-language content
                        # (if any) is persisted once, after the Output
                        # Guard resolves below - not duplicated here.
                        for tc in (getattr(history_message, "tool_calls", None) or []):
                            chat_session_repo.append_message(
                                chat_session_id, tenant_id, role="tool_call",
                                content=json.dumps(tc), tool_name=tc.get("name"), tool_call_id=tc.get("id"),
                            )
            elif "tools" in message[1]:
                for tool_message in message[1]["tools"]["messages"]:
                    if hasattr(tool_message, 'model_dump_json'):
                        context.append(tool_message.model_dump_json())
                    else:
                        context.append(json.dumps(tool_message))
                    if hasattr(tool_message, "content"):
                        tool_call_id = getattr(tool_message, "tool_call_id", None)
                        tool_evidence.append({
                            "content": _normalize_ai_message_content(tool_message.content),
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_call_names.get(tool_call_id),
                        })
                    if chat_session_repo and hasattr(tool_message, "content"):
                        chat_session_repo.append_message(
                            chat_session_id, tenant_id, role="tool_message",
                            content=_normalize_ai_message_content(tool_message.content),
                            tool_call_id=getattr(tool_message, "tool_call_id", None),
                        )
        
        # Capture AI content for post-check
        if message[0] == "messages":
            chunk = message[1][0]
            if isinstance(chunk, AIMessageChunk):
                # Real, confirmed bug found live during final verification
                # of this exact fix: a second, separate accumulation site
                # for the same chunk[0].content - missed in the first pass
                # (which only normalized the two `yield` sites) - crashed
                # with the identical "can only concatenate str (not list)
                # to str" TypeError whenever content was the list-of-
                # content-blocks shape, killing the whole streaming
                # response mid-generation with no 'end' event ever sent
                # (reproduced live: HTTP request completed in ~6s, real
                # answer text truncated mid-sentence, no server error
                # surfaced to the client at all - a silent, hard failure).
                ai_full_content += _normalize_ai_message_content(chunk.content)

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
    post_check_result = await _validate_output_guard(
        output_guard,
        ai_full_content,
        context_metadata,
    )
    output_status = _guard_status(post_check_result)
    reason_category = (post_check_result.metadata or {}).get("failure_category") or (
        post_check_result.violation_type or "none"
    ).lower()
    record_output_guard_outcome(output_status.value, reason_category)
    
    # Log Output Guard Check
    agent_audit.log_guard_check(
        guard_name="Output Guard",
        is_safe=post_check_result.is_safe,
        violation_type=post_check_result.violation_type,
        session_id=session_id,
        validation_status=output_status.value,
        model=model,
        chat_session_id=chat_session_id,
        reason_category=reason_category,
    )

    if output_status != GuardStatus.PASSED:
        logger.error(f"Output blocked by Llama Guard: {post_check_result.violation_type}")
        final_ai_content = _safe_terminal_message(
            output_status,
            post_check_result.violation_type,
        )
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
                tenant_id=tenant_id,
                user_id=user_id,
                metadata={"status": "redacted"}
            )
            # Update the last message in context with the redacted version
            for i in range(len(context) - 1, -1, -1):
                msg_data = json.loads(context[i])
                if msg_data.get("type") == "ai":
                    msg_data["content"] = redacted_content
                    context[i] = json.dumps(msg_data)
                    break
        redacted_content = post_check_result.metadata.get("redacted_content")
        final_ai_content = (
            redacted_content
            if redacted_content and redacted_content != ai_full_content
            else ai_full_content
        )

    citations = []
    if output_status == GuardStatus.PASSED and tool_evidence:
        try:
            citations = build_validated_citations(tool_evidence, tenant_id)
        except Exception as exc:
            logger.error(f"Contract Chat citation validation failed ({type(exc).__name__})")

    if chat_session_repo:
        # Exactly one final "ai_message" row per request, persisted with
        # the same content the user actually saw - safety-blocked or
        # PII-redacted, not the raw ai_full_content - so a restored
        # session never shows something the live response didn't.
        citation_kwargs = {"citations": citations} if citations else {}
        persisted = chat_session_repo.append_message(
            chat_session_id, tenant_id, role="ai_message", content=final_ai_content,
            model=model, terminal_status=output_status.value, **citation_kwargs,
        )
        if not persisted:
            raise ChatPersistenceError("chat terminal persistence failed")

    if output_status == GuardStatus.PASSED:
        yield f"data: {json.dumps({'content': final_ai_content, 'type': 'ai_message', 'status': output_status.value})}\n\n"
    else:
        yield f"data: {json.dumps({'content': final_ai_content, 'type': 'error', 'status': output_status.value})}\n\n"

    if citations:
        yield f"data: {json.dumps({'content': json.dumps(citations), 'type': 'citations'})}\n\n"

    if output_status == GuardStatus.PASSED:
        yield f"data: {json.dumps({'content': context, 'type': 'history'})}\n\n"
    yield f"data: {json.dumps({'content': '', 'type': 'end', 'status': output_status.value})}\n\n"


def _persist_chat_terminal_state(
    chat_session_id: Optional[str],
    tenant_id: str,
    model: str,
    message: str,
    terminal_status: str,
) -> None:
    """Close an interrupted persisted turn with an explicit safe state."""
    if not chat_session_id:
        return
    try:
        repository = Neo4jChatSessionRepository()
        existing = repository.list_messages(chat_session_id, tenant_id)
        if not existing or existing[-1].get("role") == "ai_message":
            return
        repository.append_message(
            chat_session_id,
            tenant_id,
            role="ai_message",
            content=message,
            model=model,
            terminal_status=terminal_status,
        )
    except Exception as exc:
        logger.error(
            f"Failed to persist Contract Chat terminal state ({type(exc).__name__}); content omitted"
        )


def _audit_chat_terminal_outcome(kwargs: dict, terminal_status: str) -> None:
    """Persist bounded terminal metadata without prompt/output/error payloads."""
    try:
        correlation_id = correlation_id_var.get() or "unknown_session"
        AuditLogger().log_event(
            event_type=AuditEventType.PROCESSING_ERROR,
            resource_id=correlation_id,
            action=f"contract_chat_{terminal_status}",
            tenant_id=kwargs["tenant_id"],
            user_id=kwargs.get("user_id") or "authenticated_user",
            status=terminal_status,
            metadata={
                "correlation_id": correlation_id,
                "chat_session_id": kwargs.get("chat_session_id"),
                "model": kwargs.get("model"),
                "terminal_status": terminal_status,
            },
        )
    except Exception as exc:
        logger.error(
            f"Failed to audit Contract Chat terminal state ({type(exc).__name__}); content omitted"
        )


async def resilient_runner(**kwargs):
    """Keep failed/cancelled SSE turns honest in the persistence layer."""
    try:
        async for event in runner(**kwargs):
            yield event
    except asyncio.CancelledError:
        _persist_chat_terminal_state(
            kwargs.get("chat_session_id"),
            kwargs["tenant_id"],
            kwargs["model"],
            "Response cancelled before completion.",
            GuardStatus.CANCELLED.value,
        )
        record_output_guard_outcome(GuardStatus.CANCELLED.value, "client_cancellation")
        _audit_chat_terminal_outcome(kwargs, GuardStatus.CANCELLED.value)
        raise
    except Exception as exc:
        logger.error(
            f"Contract Chat stream failed ({type(exc).__name__}); prompt and content omitted"
        )
        message = "Response failed before completion. Please retry."
        terminal_status = (
            "persistence_failed"
            if isinstance(exc, ChatPersistenceError)
            else "generation_failed"
        )
        _persist_chat_terminal_state(
            kwargs.get("chat_session_id"),
            kwargs["tenant_id"],
            kwargs["model"],
            message,
            terminal_status,
        )
        _audit_chat_terminal_outcome(kwargs, terminal_status)
        yield f"data: {json.dumps({'content': message, 'type': 'error', 'status': terminal_status})}\n\n"
        yield f"data: {json.dumps({'content': '', 'type': 'end', 'status': terminal_status})}\n\n"


@app.post("/api/run/")
async def run(
    payload: RunPayload,
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    effective_contract_id = normalize_contract_scope(payload.contract_id)
    if payload.session_id:
        # Ownership check happens here, not inside runner(): runner() is an
        # async generator feeding StreamingResponse, and by the time it
        # could raise, response headers (200, text/event-stream) may
        # already be committed, so a clean HTTPException(404) isn't
        # reliably achievable from inside it. This is a plain coroutine.
        session = Neo4jChatSessionRepository().get_session(payload.session_id, identity.tenant_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {payload.session_id} not found")
        persisted_contract_id = normalize_contract_scope(session.get("contract_id"))
        if effective_contract_id != persisted_contract_id:
            # Reject before StreamingResponse starts and before runner()
            # appends the user turn, so the stored conversation remains
            # unchanged after an attempted scope override.
            raise HTTPException(status_code=409, detail="Contract scope does not match chat session")
        effective_contract_id = persisted_contract_id

    if effective_contract_id and not contract_exists_for_tenant(
        effective_contract_id, identity.tenant_id
    ):
        # Same response for a missing contract and one owned by another
        # tenant; the tenant predicate is inside the Neo4j query.
        raise HTTPException(status_code=404, detail="Contract not found")

    return StreamingResponse(
        resilient_runner(
            model=payload.model,
            prompt=payload.prompt,
            history=payload.history or "[]",
            llm_mgr=llm_mgr,
            tenant_id=identity.tenant_id,
            user_role=identity.role,
            user_id=identity.username or "authenticated_user",
            contract_id=effective_contract_id,
            chat_session_id=payload.session_id,
        ),
        media_type="text/event-stream",
    )
