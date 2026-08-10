import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST

from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, AIMessageChunk
from backend.llm_manager import LLMManager
from backend.model_registry import ModelSelectionError, model_spec, validate_model
from backend.contract_chat_agent import CHAT_PROMPT_VERSION
from backend.api.document_upload import router as document_router
from backend.api.model_registry_api import router as model_registry_router
from backend.api.contract_intelligence import router as intelligence_router
from backend.api.routes.debug import create_debug_router
from backend.shared.utils.route_utils import is_development, is_production, conditionally_include_router
from backend.api.enhanced_contract_search import router as enhanced_search_router
from backend.api.enhanced_document_upload import router as enhanced_upload_router
from backend.agents.agent_workflow_tracker import get_current_workflow_status
from backend.shared.middleware.tracing import TracingMiddleware
from backend.shared.middleware.metrics import PrometheusMiddleware
from backend.shared.middleware.security_headers import SecurityHeadersMiddleware
from backend.shared.middleware.rate_limit import limiter, CHAT_RUN_RATE_LIMIT, tenant_scoped_or_ip_key
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
from backend.application.services.chat_evidence_service import (
    combine_evidence_envelopes,
    evidence_summary,
    parse_evidence_envelope,
    render_deterministic_metadata_answer,
)
from backend.shared.monitoring.prometheus_metrics import record_output_guard_outcome

logger = get_logger(__name__)

_SAFE_TERMINAL_REASONS = {
    "none",
    "no_evidence",
    "unsupported_claim",
    "contradicted_claim",
    "insufficient_scope",
    "count_mismatch",
    "invented_contract",
    "fabricated_evidence_id",
    "unauthorized_evidence",
    "cross_tenant_evidence",
    "text_evidence_required",
    "infrastructure",
    "invalid_evidence_envelope",
    "invalid_result",
    "timeout",
    "empty_output",
    "unsafe_output",
    "out_of_scope",
    "malicious_intent",
    "prompt_guard_rejection",
    "client_cancellation",
    "generation_failed",
    "generation_timeout",
    "persistence_failed",
}


def _bounded_reason_category(value: object, fallback: str) -> str:
    candidate = value.lower() if isinstance(value, str) else fallback
    return candidate if candidate in _SAFE_TERMINAL_REASONS else fallback


class ChatPersistenceError(RuntimeError):
    """A safe, content-free marker for terminal-message persistence failure."""


class ChatGenerationTimeoutError(RuntimeError):
    """A safe, content-free marker for a stalled/hung generation stream -
    reconciliation-audit finding: runner()'s astream loop had no timeout at
    all, unlike every other provider-facing call in this engagement (PDF
    extraction's EXTRACTION_TIMEOUT_SECONDS, reranking's
    RERANKER_TIMEOUT_SECONDS, Output Guard's OUTPUT_GUARD_TIMEOUT_SECONDS -
    see ADR-004). A hung provider stream could otherwise hold the request
    (and the worker/connection serving it) open indefinitely."""


@dataclass
class ActiveChatRun:
    """Server-owned cancellation state for one authenticated SSE run."""

    run_id: str
    tenant_id: str
    session_id: str
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: Optional[str] = None


class ChatRunRegistry:
    """Process-local active-run registry for the single-process API service.

    The production deployment runs one constrained FastAPI process. Keeping the
    cancellable asyncio task in that process is what permits immediate provider,
    tool, and Output Guard cancellation. Any future multi-process API deployment
    requires a distributed cancellation design; a Redis flag alone cannot cancel
    an asyncio task executing in another process.
    """

    def __init__(self):
        self._runs: dict[str, ActiveChatRun] = {}
        self._lock = asyncio.Lock()

    async def register(self, run_id: str, tenant_id: str, session_id: str) -> ActiveChatRun:
        async with self._lock:
            if run_id in self._runs:
                raise ValueError("run identifier is already active")
            run = ActiveChatRun(run_id=run_id, tenant_id=tenant_id, session_id=session_id)
            self._runs[run_id] = run
            return run

    async def request_cancel(
        self,
        run_id: str,
        tenant_id: str,
        session_id: str,
        timeout_seconds: float = 10.0,
    ) -> Optional[str]:
        async with self._lock:
            run = self._runs.get(run_id)
            if not run or run.tenant_id != tenant_id or run.session_id != session_id:
                return None
            run.cancel_requested.set()

        try:
            await asyncio.wait_for(run.done.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return "cancellation_timeout"
        return run.outcome or "cancellation_failed"

    async def finish(self, run: ActiveChatRun) -> None:
        async with self._lock:
            if self._runs.get(run.run_id) is run:
                self._runs.pop(run.run_id, None)
        run.done.set()


chat_run_registry = ChatRunRegistry()

import os
from openinference.instrumentation.langchain import LangChainInstrumentor

load_dotenv()

# Reconciliation-audit finding: bounds a STALLED generation stream (no new
# chunk at all), not the turn's total duration - a total-duration cap would
# require buffering every tool_call/tool_message/user_message event instead
# of streaming them live as they arrive (the existing, deliberate behavior
# at runner()'s "messages"/"updates" yield sites), and would kill
# legitimately slow-but-progressing multi-tool-call turns. A per-chunk idle
# timeout, like a network read-timeout, only trips when the provider has
# genuinely gone silent - which is exactly what "hung/runaway" describes.
# Same order of magnitude as OUTPUT_GUARD_TIMEOUT_SECONDS's default
# (ADR-004) and the other provider-facing timeouts elsewhere in this
# engagement (EXTRACTION_TIMEOUT_SECONDS, RERANKER_TIMEOUT_SECONDS).
GENERATION_STALL_TIMEOUT_SECONDS = float(os.getenv("GENERATION_STALL_TIMEOUT_SECONDS", "60"))

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
app.include_router(model_registry_router)
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
    # Client-generated opaque UUID used only to cancel this authenticated,
    # tenant/session-bound active request. It is never authorization by itself.
    run_id: Optional[UUID] = None


class CancelChatRunPayload(BaseModel):
    session_id: str

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


def _safe_terminal_message(
    status: GuardStatus,
    violation_type: Optional[str],
    reason_category: Optional[str] = None,
) -> str:
    if status == GuardStatus.REJECTED and reason_category == "no_evidence":
        return "No relevant contract evidence was found for this question. Try narrowing the contract or rephrasing the request."
    if status == GuardStatus.REJECTED and violation_type in {
        "UNGROUNDED_OUTPUT",
        "HALLUCINATION_DETECTED",
        "UNKNOWN_EVIDENCE_ID",
        "CONTRADICTED_OUTPUT",
    }:
        return "Response withheld because the answer contained claims that could not be verified against the selected contract evidence."
    if status == GuardStatus.REJECTED:
        return "Response withheld by the safety policy. Please revise your request or retry."
    if status == GuardStatus.TIMED_OUT:
        return "Response verification timed out. Please retry."
    if status == GuardStatus.EMPTY:
        return "The assistant returned no response. Please retry."
    return "The response could not be validated because the verification service failed. Please retry."


def _safe_prompt_guard_message(violation_type: Optional[str]) -> str:
    if violation_type == "OUT_OF_SCOPE":
        return "Contract Chat is limited to contract-related requests. Please ask a question about the selected contract evidence."
    return "This request was blocked by the Contract Chat safety policy. Please revise it and retry."


async def runner(model: str, prompt: str, history: str, llm_mgr: LLMManager, tenant_id: str, user_role: str = "unknown", user_id: str = "authenticated_user", contract_id: Optional[str] = None, chat_session_id: Optional[str] = None, requested_provider: Optional[str] = None, actual_provider: Optional[str] = None):
    logger.info(f"Processing LLM request for model '{model}' for user_role '{user_role}'")
    requested_provider = requested_provider or model_spec(model).provider
    actual_provider = actual_provider or requested_provider

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
        # The API validated this stable ID before runner entry. Output Guard
        # uses the same exact provider boundary as generation, never a hidden
        # Gemini validator or cross-provider substitute.
        "model": model,
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
        prompt_guard_message = _safe_prompt_guard_message(guard_result.violation_type)
        prompt_metadata = getattr(guard_result, "metadata", None)
        prompt_reason_value = (
            prompt_metadata.get("failure_category")
            if isinstance(prompt_metadata, dict)
            else None
        )
        prompt_reason = _bounded_reason_category(
            prompt_reason_value
            if isinstance(prompt_reason_value, str)
            else guard_result.violation_type,
            "prompt_guard_rejection",
        )
        if chat_session_repo:
            # Still visible on reopen - a declined prompt shouldn't vanish
            # from a restored session just because it never reached the LLM.
            chat_session_repo.append_message(chat_session_id, tenant_id, role="user_message", content=prompt)
            chat_session_repo.append_message(
                chat_session_id,
                tenant_id,
                role="ai_message",
                content=prompt_guard_message,
                requested_model=model,
                requested_provider=requested_provider,
                prompt_version=CHAT_PROMPT_VERSION,
                execution_path="contract_chat_langgraph",
                terminal_status=prompt_guard_status.value,
                terminal_reason=prompt_reason,
            )
        yield f"data: {json.dumps({'content': prompt_guard_message, 'type': 'error', 'status': prompt_guard_status.value, 'reason_category': prompt_reason})}\n\n"
        yield f"data: {json.dumps({'content': '', 'type': 'end', 'status': prompt_guard_status.value, 'reason_category': prompt_reason})}\n\n"
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
    evidence_envelopes = []

    messages_iter = messages.__aiter__()
    while True:
        try:
            message = await asyncio.wait_for(
                messages_iter.__anext__(), timeout=GENERATION_STALL_TIMEOUT_SECONDS,
            )
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            logger.error(
                f"Chat generation stalled - no output for {GENERATION_STALL_TIMEOUT_SECONDS}s; aborting"
            )
            # Caught by resilient_runner's except Exception block, which
            # persists the terminal record, audits, and yields the safe
            # error+end SSE events - the same path already used for every
            # other mid-stream failure (ChatPersistenceError, provider
            # errors). No content was safely completed here, so nothing
            # else in this function needs to run.
            raise ChatGenerationTimeoutError(
                f"generation stalled for {GENERATION_STALL_TIMEOUT_SECONDS}s"
            )

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
                        normalized_content = _normalize_ai_message_content(tool_message.content)
                        parsed_envelope = parse_evidence_envelope(normalized_content)
                        if parsed_envelope:
                            evidence_envelopes.append(parsed_envelope)
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

    # The exact structured envelopes that the answer model saw are combined
    # for Output Guard and citation validation.  Historical tool messages are
    # deliberately excluded: only evidence retrieved for this turn may ground
    # its candidate answer.
    evidence_envelope = combine_evidence_envelopes(evidence_envelopes, tenant_id)
    context_metadata["evidence_envelope"] = evidence_envelope
    logger.info("Contract Chat evidence prepared: %s", evidence_summary(evidence_envelope))
    deterministic_metadata_answer = render_deterministic_metadata_answer(
        prompt,
        evidence_envelope,
    )
    if deterministic_metadata_answer is not None:
        ai_full_content = deterministic_metadata_answer
        logger.info("Using deterministic metadata answer from validated evidence")

    output_guard = OutputGuard(audit_logger=audit_logger, model_manager=llm_mgr)
    post_check_result = await _validate_output_guard(
        output_guard,
        ai_full_content,
        context_metadata,
    )
    output_status = _guard_status(post_check_result)
    raw_reason_category = (post_check_result.metadata or {}).get("failure_category") or (
        post_check_result.violation_type or "none"
    ).lower()
    reason_category = _bounded_reason_category(raw_reason_category, "infrastructure")
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
            reason_category,
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
    if output_status == GuardStatus.PASSED and evidence_envelope.get("evidence"):
        try:
            citations = build_validated_citations([evidence_envelope], tenant_id, answer_text=final_ai_content)
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
            model=model, terminal_status=output_status.value,
            terminal_reason=reason_category,
            requested_model=model, actual_model=model,
            requested_provider=requested_provider, actual_provider=actual_provider,
            fallback_occurred=False, prompt_version=CHAT_PROMPT_VERSION,
            execution_path="contract_chat_langgraph", **citation_kwargs,
        )
        if not persisted:
            raise ChatPersistenceError("chat terminal persistence failed")

    if output_status == GuardStatus.PASSED:
        yield f"data: {json.dumps({'content': final_ai_content, 'type': 'ai_message', 'status': output_status.value, 'requested_model': model, 'actual_model': model, 'requested_provider': requested_provider, 'actual_provider': actual_provider, 'fallback_occurred': False, 'prompt_version': CHAT_PROMPT_VERSION, 'execution_path': 'contract_chat_langgraph'})}\n\n"
    else:
        yield f"data: {json.dumps({'content': final_ai_content, 'type': 'error', 'status': output_status.value, 'reason_category': reason_category})}\n\n"

    if citations:
        yield f"data: {json.dumps({'content': json.dumps(citations), 'type': 'citations'})}\n\n"

    if output_status == GuardStatus.PASSED:
        yield f"data: {json.dumps({'content': context, 'type': 'history'})}\n\n"
    yield f"data: {json.dumps({'content': '', 'type': 'end', 'status': output_status.value, 'reason_category': reason_category})}\n\n"


def _persist_chat_terminal_state(
    chat_session_id: Optional[str],
    tenant_id: str,
    model: str,
    message: str,
    terminal_status: str,
    provider: Optional[str] = None,
    terminal_reason: Optional[str] = None,
) -> Optional[bool]:
    """Close an interrupted turn if no assistant terminal already won.

    The tri-state return is the race result: true when this call created the
    terminal record, false when an assistant terminal already won, and `None`
    when persistence could not be confirmed.
    """
    if not chat_session_id:
        return None
    try:
        repository = Neo4jChatSessionRepository()
        existing = repository.list_messages(chat_session_id, tenant_id)
        if not existing:
            return None
        if existing[-1].get("role") == "ai_message":
            return False
        persisted = repository.append_message(
            chat_session_id,
            tenant_id,
            role="ai_message",
            content=message,
            model=model,
            terminal_status=terminal_status,
            terminal_reason=terminal_reason or terminal_status,
            requested_model=model,
            actual_model=model,
            requested_provider=provider,
            actual_provider=provider,
            fallback_occurred=False,
            prompt_version=CHAT_PROMPT_VERSION,
            execution_path="contract_chat_langgraph",
        )
        return True if persisted else None
    except Exception as exc:
        logger.error(
            f"Failed to persist Contract Chat terminal state ({type(exc).__name__}); content omitted"
        )
        return None


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


async def resilient_runner(
    cancellation_observer: Optional[Callable[[Optional[bool]], None]] = None,
    **kwargs,
):
    """Keep failed/cancelled SSE turns honest in the persistence layer."""
    try:
        async for event in runner(**kwargs):
            yield event
    except asyncio.CancelledError:
        cancellation_won = _persist_chat_terminal_state(
            kwargs.get("chat_session_id"),
            kwargs["tenant_id"],
            kwargs["model"],
            "Generation stopped",
            GuardStatus.CANCELLED.value,
            kwargs.get("actual_provider"),
            "client_cancellation",
        )
        if cancellation_observer:
            cancellation_observer(cancellation_won)
        if cancellation_won:
            record_output_guard_outcome(GuardStatus.CANCELLED.value, "client_cancellation")
            _audit_chat_terminal_outcome(kwargs, GuardStatus.CANCELLED.value)
        raise
    except Exception as exc:
        logger.error(
            f"Contract Chat stream failed ({type(exc).__name__}); prompt and content omitted"
        )
        if isinstance(exc, ChatPersistenceError):
            message = "Response failed before completion. Please retry."
            terminal_status = "persistence_failed"
        elif isinstance(exc, ChatGenerationTimeoutError):
            message = "Response generation timed out. Please retry."
            terminal_status = "generation_timeout"
        else:
            message = "Response failed before completion. Please retry."
            terminal_status = "generation_failed"
        _persist_chat_terminal_state(
            kwargs.get("chat_session_id"),
            kwargs["tenant_id"],
            kwargs["model"],
            message,
            terminal_status,
            kwargs.get("actual_provider"),
            terminal_status,
        )
        _audit_chat_terminal_outcome(kwargs, terminal_status)
        yield f"data: {json.dumps({'content': message, 'type': 'error', 'status': terminal_status, 'reason_category': terminal_status})}\n\n"
        yield f"data: {json.dumps({'content': '', 'type': 'end', 'status': terminal_status, 'reason_category': terminal_status})}\n\n"


def _is_durably_terminal_sse_event(event: str) -> bool:
    """Return true once the browser-visible terminal record is persisted."""
    try:
        payload = json.loads(event.removeprefix("data: ").strip())
    except (TypeError, ValueError):
        return False
    return payload.get("type") in {"ai_message", "error"} and bool(payload.get("status"))


async def cancellable_chat_stream(
    run: ActiveChatRun,
    **runner_kwargs,
) -> AsyncIterator[str]:
    """Race each stream step against an authenticated server cancellation.

    Cancelling the pending `anext` task injects `CancelledError` through
    `resilient_runner` into provider/tool/Output Guard awaits. That path persists
    `cancelled` before the cancellation endpoint acknowledges success.
    """

    def observe_cancellation(persisted: Optional[bool]) -> None:
        if persisted is True:
            run.outcome = "cancelled"
        elif persisted is False:
            run.outcome = "completed"
        else:
            run.outcome = "cancellation_failed"

    source = resilient_runner(
        cancellation_observer=observe_cancellation,
        **runner_kwargs,
    )
    next_event: Optional[asyncio.Task] = None
    cancellation_wait: Optional[asyncio.Task] = None
    try:
        while True:
            next_event = asyncio.create_task(source.__anext__())
            cancellation_wait = asyncio.create_task(run.cancel_requested.wait())
            done, _ = await asyncio.wait(
                {next_event, cancellation_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if next_event in done:
                cancellation_wait.cancel()
                try:
                    await cancellation_wait
                except asyncio.CancelledError:
                    pass
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    break

                if _is_durably_terminal_sse_event(event):
                    run.outcome = "completed"
                    yield event
                    async for remaining in source:
                        yield remaining
                    break

                yield event
                if run.cancel_requested.is_set():
                    # Cancellation and a non-terminal chunk became ready in the
                    # same loop turn. Never release a later assistant answer.
                    continue
            else:
                next_event.cancel()
                try:
                    await next_event
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
                break
    finally:
        for pending in (next_event, cancellation_wait):
            if pending and not pending.done():
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
        try:
            await source.aclose()
        except (RuntimeError, asyncio.CancelledError):
            pass
        if run.outcome is None:
            run.outcome = "completed_or_unpersisted"
        await chat_run_registry.finish(run)


@app.post("/api/chat/runs/{run_id}/cancel", status_code=202)
async def cancel_chat_run(
    run_id: UUID,
    payload: CancelChatRunPayload,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    # Revalidate durable session ownership; registry possession is never auth.
    session = Neo4jChatSessionRepository().get_session(
        payload.session_id,
        identity.tenant_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active chat run not found")

    outcome = await chat_run_registry.request_cancel(
        str(run_id),
        identity.tenant_id,
        payload.session_id,
    )
    if outcome is None:
        raise HTTPException(status_code=404, detail="Active chat run not found")
    if outcome == "completed":
        raise HTTPException(status_code=409, detail="Chat run already completed")
    if outcome != "cancelled":
        raise HTTPException(status_code=503, detail="Chat cancellation could not be confirmed")
    return {"status": "cancelled"}


@app.post("/api/run/")
@limiter.limit(CHAT_RUN_RATE_LIMIT, key_func=tenant_scoped_or_ip_key)
async def run(
    request: Request,
    payload: RunPayload,
    llm_mgr: LLMManager = Depends(get_llm_manager),
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Rate-limited per-tenant (CHAT_RUN_RATE_LIMIT, reconciliation-audit
    finding) - every call is a real, billed LLM generation. The
    `request: Request` parameter (unused directly here) is required by
    @limiter.limit to identify the calling client, same convention as
    auth_api.py's register()/issue_token()."""
    try:
        selected_spec = validate_model(payload.model, "chat")
    except ModelSelectionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "category": exc.category},
        )
    if payload.model not in llm_mgr.agents:
        raise HTTPException(status_code=503, detail="Selected model is temporarily unavailable")
    if payload.run_id and not payload.session_id:
        raise HTTPException(status_code=400, detail="Cancellable chat runs require a session")
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

    runner_kwargs = {
        "model": payload.model,
        "prompt": payload.prompt,
        "history": payload.history or "[]",
        "llm_mgr": llm_mgr,
        "tenant_id": identity.tenant_id,
        "user_role": identity.role,
        "user_id": identity.username or "authenticated_user",
        "contract_id": effective_contract_id,
        "chat_session_id": payload.session_id,
        "requested_provider": selected_spec.provider,
        "actual_provider": selected_spec.provider,
    }
    if payload.run_id and payload.session_id:
        try:
            active_run = await chat_run_registry.register(
                str(payload.run_id),
                identity.tenant_id,
                payload.session_id,
            )
        except ValueError:
            raise HTTPException(status_code=409, detail="Chat run identifier is already active")
        stream = cancellable_chat_stream(active_run, **runner_kwargs)
    else:
        stream = resilient_runner(**runner_kwargs)

    return StreamingResponse(stream, media_type="text/event-stream")
