import asyncio
import base64
import inspect
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, List, Optional
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST

from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
from backend.llm_manager import LLMManager
from backend.model_registry import ModelSelectionError, model_spec, validate_model
from backend.routing_service import AUTO_MODEL_ID, route_chat_model
from backend.contract_chat_agent import CHAT_PROMPT_VERSION
from backend.api.document_upload import router as document_router
from backend.api.model_registry_api import router as model_registry_router
from backend.api.admin_evaluations_api import router as admin_evaluations_router
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
from backend.infrastructure.chat_attachment_storage import chat_attachment_storage
from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
from backend.application.services.chat_citation_service import build_validated_citations
from backend.application.services.chat_evidence_service import (
    combine_evidence_envelopes,
    evidence_summary,
    image_attachment_evidence_item,
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

# Agentic self-correction (runner()'s Output Guard retry): how many times a
# CONTRADICTED_OUTPUT/HALLUCINATION_DETECTED verdict gets fed back to the
# generator as revision feedback before falling back to the safe rejection
# message. Each attempt is a single extra raw-model call (revise the answer
# against the SAME already-retrieved evidence, never new tool calls), fully
# re-validated by Output Guard - never a bypass, only another chance to pass
# the same real check. 2 matches HallucinationValidator's own
# MAX_AUDIT_ATTEMPTS order of magnitude (ADR-004 addendum).
MAX_GENERATION_RETRIES = int(os.getenv("MAX_GENERATION_RETRIES", "2"))

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

    # Auto-seed default user accounts (demo and admin) if database is available
    try:
        from backend.infrastructure.user_repository import UserRepository
        user_repo = UserRepository()
        for uname, pwd, tenant, role in [
            ("demo", "password123", "demo_tenant", "ANALYST"),
            ("admin", "Password123!", "tenant_alpha", "ADMIN"),
        ]:
            if not user_repo.get_user_by_username(uname):
                user_repo.create_user(uname, pwd, tenant, role, enforce_tenant_bootstrap=False)
                logger.info(f"Lifespan auto-seeded default account '{uname}'")
    except Exception as exc:
        logger.warning(f"Lifespan auto-seeding default users skipped/failed: {exc}")

    yield
    # Shutdown - cleanup if needed

app = FastAPI(lifespan=lifespan)

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager


def _get_cors_origins() -> list:
    """
    Production-readiness audit finding #2.
    Dev stays permissive (*). Production requires CORS_ALLOWED_ORIGINS list.
    """
    if not is_production():
        return ["*"]

    origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if not origins:
        logger.warning("CORS_ALLOWED_ORIGINS is not set in production")
    return origins


# Add all middlewares FIRST before any exception handlers or routers touch/build the middleware stack
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_get_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(TracingMiddleware)
app.add_middleware(SlowAPIMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include routers based on environment
app.include_router(document_router)
app.include_router(model_registry_router)
app.include_router(admin_evaluations_router)
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


# ADR-008: bounds cost/context blowup per turn; easy to raise later since
# it's already a proper relationship (HAS_ATTACHMENT), not a single field.
MAX_ATTACHMENTS_PER_MESSAGE = 4


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
    # Optional: image attachments (ADR-008) uploaded ahead of this turn via
    # POST /api/chat/sessions/{session_id}/attachments. Requires
    # session_id (attachments are session-scoped; there is no ownership
    # context to check them against otherwise) and a vision-capable model
    # (model_registry.py's ModelSpec.capabilities) - both enforced in the
    # /api/run/ route below, before streaming starts, same "clean 400
    # before StreamingResponse starts" reasoning as every other pre-stream
    # validation here.
    attachment_ids: Optional[List[str]] = None


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


# Real, confirmed bug found live (multi-turn image context): a session's
# persisted content is always plain text, even for a turn that carried an
# image (ADR-008 - only the HAS_ATTACHMENT relationship records that), so a
# later turn's model had zero way to know an image was ever attached at
# all, and would either hallucinate about it or get confused into a forced,
# irrelevant tool call. This marker restores just enough context - not the
# image itself - for the model to give an honest "I don't have access to
# that anymore" answer instead, for any image-bearing turn OLDER than the
# single most-recent one (see _messages_from_stored's docstring below for
# that one's real carry-forward treatment). contract_chat_agent.py's
# BASE_SYSTEM_PROMPT quotes this exact string so the model recognizes it.
_HISTORICAL_IMAGE_MARKER = (
    "\n\n[This message included an attached image that is not available in "
    "the current turn.]"
)


def _messages_from_stored(stored_messages, chat_session_repo=None, tenant_id=None, chat_session_id=None):
    """Rebuilds LangChain history for the LLM from persisted ChatMessage
    rows (Neo4jChatSessionRepository.list_messages), used instead of
    rebuild_history() whenever a session_id is present. Returns
    (messages, carried_forward_attachments) - the second element is the
    (attachment_id, mime_type) pairs of any image carried forward for real
    this call, in the same shape _build_prompt_message's loaded_attachments
    already is, so the caller can fold it into the same Output Guard
    evidence-building loop with zero new evidence code.

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

    ADR-008 cross-turn image context (bounded, not unbounded replay): only
    the SINGLE most recent user_message row with has_attachment=True gets
    its real image bytes re-loaded and attached as real content blocks -
    every OLDER image-bearing row still gets the plain-text
    _HISTORICAL_IMAGE_MARKER, unchanged from before. This keeps the added
    provider cost/latency of carrying an image forward constant per turn
    (at most one turn's worth of images, capped the same way a single
    turn's own attachments already are - MAX_ATTACHMENTS_PER_MESSAGE)
    regardless of how long the conversation continues after that, rather
    than growing with the full session history. Attaching a NEW image on a
    later turn naturally supersedes this: that later turn becomes "the
    most recent," and this one reverts to the marker on the turn after
    that. If the carried-forward attachment(s) can no longer be loaded
    (e.g. deleted since), this degrades to the same marker rather than
    silently omitting the image with no explanation - see
    _build_prompt_message's own vanished-attachment handling for the same
    posture on the current turn's own attachments.
    """
    role_to_class = {"user_message": HumanMessage, "ai_message": AIMessage}

    most_recent_image_message_id = None
    for row in stored_messages:
        if row.get("role") == "user_message" and row.get("has_attachment"):
            most_recent_image_message_id = row.get("message_id")

    carried_forward_attachments: list = []
    messages = []
    for row in stored_messages:
        message_class = role_to_class.get(row.get("role"))
        if not message_class:
            continue
        content = row.get("content") or ""
        is_image_row = row.get("role") == "user_message" and row.get("has_attachment")
        is_most_recent_image_row = is_image_row and row.get("message_id") == most_recent_image_message_id

        if is_most_recent_image_row and chat_session_repo is not None:
            attachments = chat_session_repo.list_attachments_for_message(row["message_id"], tenant_id)
            image_blocks, loaded = _image_content_blocks(
                [a["attachment_id"] for a in attachments], chat_session_repo, tenant_id, chat_session_id,
            )
            if image_blocks:
                messages.append(HumanMessage(content=[{"type": "text", "text": content}, *image_blocks]))
                carried_forward_attachments.extend(loaded)
                continue

        if is_image_row:
            content += _HISTORICAL_IMAGE_MARKER
        messages.append(message_class(content=content))
    return messages, carried_forward_attachments


def _image_content_blocks(
    attachment_ids: Optional[List[str]],
    chat_session_repo,
    tenant_id: str,
    chat_session_id: Optional[str],
) -> tuple[list, list]:
    """Loads each attachment_id's real bytes (ownership-checked via
    get_attachment) and returns (image_blocks, loaded_attachments) - the
    provider-agnostic content-block format (langchain_core's documented v1
    standard, ImageContentBlock: {"type": "image", "base64"|"url",
    "mime_type"}) plus the (attachment_id, mime_type) pairs the caller
    needs to build matching Output Guard evidence (ADR-004 addendum)
    without a second repository round-trip.

    Shared by _build_prompt_message (the current turn's own attachments)
    and _messages_from_stored (ADR-008 cross-turn follow-up: the single
    most recent image-bearing turn's attachments, carried forward into
    later turns) - one implementation, not duplicated.
    """
    image_blocks: list = []
    loaded_attachments: list = []
    if not attachment_ids or not chat_session_repo:
        return image_blocks, loaded_attachments
    for attachment_id in attachment_ids:
        attachment = chat_session_repo.get_attachment(attachment_id, tenant_id, chat_session_id)
        if not attachment:
            # Already ownership-checked in the /api/run/ route before
            # streaming started - reaching here means the attachment
            # vanished in the narrow window since (e.g. a concurrent
            # request). Skip it rather than fail the whole turn over one
            # since-vanished attachment.
            continue
        image_bytes = chat_attachment_storage.read(
            tenant_id, chat_session_id, attachment_id, attachment["storage_key"],
        )
        image_blocks.append({
            "type": "image",
            "base64": base64.b64encode(image_bytes).decode("ascii"),
            "mime_type": attachment["mime_type"],
        })
        loaded_attachments.append({"attachment_id": attachment_id, "mime_type": attachment["mime_type"]})
    return image_blocks, loaded_attachments


def _build_prompt_message(
    prompt: str,
    attachment_ids: Optional[List[str]],
    chat_session_repo,
    tenant_id: str,
    chat_session_id: Optional[str],
) -> tuple[HumanMessage, list]:
    """Plain-text HumanMessage when there's no attachment (unchanged
    behavior); otherwise a multimodal content-block list. See
    _image_content_blocks for the shared loading logic and the
    provider-agnostic content-block format (Gemini/GPT-4o/Claude's own
    LangChain integrations each independently detect and convert this to
    their own real wire shape internally - see ADR-008's research
    citations; no provider-specific branching here or anywhere else in
    this codebase).
    """
    if not attachment_ids or not chat_session_repo:
        return HumanMessage(content=prompt), []
    image_blocks, loaded_attachments = _image_content_blocks(
        attachment_ids, chat_session_repo, tenant_id, chat_session_id,
    )
    content_blocks: list = [{"type": "text", "text": prompt}, *image_blocks]
    return HumanMessage(content=content_blocks), loaded_attachments


def _history_safe_json(message: HumanMessage) -> str:
    """Same wire content as message.model_dump_json(), but with any
    "image" content block's raw base64 payload stripped out.

    Real, confirmed bug: the terminal "history" SSE event (yielded near the
    end of runner(), below) echoes this exact serialization back to the
    client, and for an image-attached turn that meant embedding the full
    multi-MB base64 image payload a second time, on the wire, for zero
    benefit - input.tsx's onmessage handler no-ops on type "history"
    entirely now that session persistence (chat_session_repo) is the
    authoritative restore path. Only prompt_message (the current turn's own
    HumanMessage) can ever carry an image block here - attachment_ids
    requires session_id (see RunPayload.attachment_ids's docstring), and
    the session_id path never round-trips a client-supplied `history`
    string back through rebuild_history() at all, so there is no path by
    which a stripped block could ever come back and be fed to a model as
    real image content.
    """
    if not isinstance(message.content, list):
        return message.model_dump_json()
    dumped = message.model_dump(mode="json")
    dumped["content"] = [
        {**block, "base64": "[omitted]"} if isinstance(block, dict) and block.get("type") == "image" else block
        for block in dumped["content"]
    ]
    return json.dumps(dumped)


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


async def runner(model: str, prompt: str, history: str, llm_mgr: LLMManager, tenant_id: str, user_role: str = "unknown", user_id: str = "authenticated_user", contract_id: Optional[str] = None, chat_session_id: Optional[str] = None, requested_provider: Optional[str] = None, actual_provider: Optional[str] = None, attachment_ids: Optional[List[str]] = None):
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
        previous_messages, carried_forward_attachments = _messages_from_stored(
            stored_messages, chat_session_repo, tenant_id, chat_session_id,
        )
        persisted_user_message = chat_session_repo.append_message(chat_session_id, tenant_id, role="user_message", content=prompt)
        if attachment_ids and persisted_user_message:
            # Links each already-uploaded, already-ownership-checked
            # attachment (ADR-008) to the message just persisted for this
            # turn - the second half of the two-phase upload-then-send
            # flow. content itself stays plain text; the attachment
            # relationship is the only record of the image.
            for attachment_id in attachment_ids:
                chat_session_repo.link_attachment_to_message(attachment_id, persisted_user_message["message_id"], tenant_id)
    elif history != "[]":
        # history comes in from FE as stringified list of dumped model messages
        previous_messages = rebuild_history(history)
        carried_forward_attachments = []
    else:
        previous_messages = []
        carried_forward_attachments = []

    prompt_message, loaded_attachments = _build_prompt_message(prompt, attachment_ids, chat_session_repo, tenant_id, chat_session_id)
    # ADR-008 cross-turn image context: the carried-forward image (if any)
    # is real evidence the responding model actually saw this turn too,
    # same as the current turn's own attachment - folded into the same
    # loaded_attachments list so the existing image_attachment_evidence_item
    # loop below covers both with no new evidence code.
    loaded_attachments = [*loaded_attachments, *carried_forward_attachments]
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
    context.append(_history_safe_json(prompt_message))
    
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
                # chunk[0].content is a plain string normally, but a list of
                # content blocks (text + image, ADR-008) when this turn
                # carried an attachment - _normalize_ai_message_content
                # already knows how to flatten that shape to just its text
                # portion (image blocks have no "text" key, so they're
                # skipped here, not serialized into this echo event).
                # Attachment representation for the live/restored UI is
                # Stage 3's scope, not this SSE echo.
                yield f"data: {json.dumps({'content': _normalize_ai_message_content(chunk[0].content), 'type': 'user_message'})}\n\n"

        if message[0] == "updates":
            # use pydantic BaseClass method model_dump_json to dump message model to be stringified into history
            if "assistant" in message[1]:
                for history_message in message[1]["assistant"]["messages"]:
                    context.append(history_message.model_dump_json())
                    # Real, confirmed bug found live (multi-turn image
                    # context investigation): the assistant node can invoke
                    # the LLM more than once per turn (e.g. a real first
                    # answer with no tool_calls, discarded and overridden by
                    # the forced-retrieval fallback below - see
                    # contract_chat_agent.py's assistant()). "messages"
                    # stream mode streams tokens from EVERY underlying LLM
                    # call inside the node, regardless of what the node
                    # ultimately returns, so accumulating ai_full_content
                    # from raw "messages" chunks double-counted the
                    # discarded first answer's text alongside the real
                    # final answer - reproduced live as the exact same
                    # sentence appearing twice, concatenated with no space,
                    # which HallucinationValidator then correctly (from its
                    # own perspective) flagged as an unsupported/
                    # contradicted claim. This "updates" stream's assistant
                    # message content is the graph's own authoritative
                    # per-step return value (same source tool_calls below
                    # already trusts over the raw token stream) - assigning
                    # (not appending) here means only the LAST assistant
                    # step's content survives, which is always the true
                    # final answer: the graph only stops once a response
                    # has no further tool_calls.
                    ai_full_content = _normalize_ai_message_content(history_message.content)
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

    # The exact structured envelopes that the answer model saw are combined
    # for Output Guard and citation validation.  Historical tool messages are
    # deliberately excluded: only evidence retrieved for this turn may ground
    # its candidate answer.
    evidence_envelope = combine_evidence_envelopes(evidence_envelopes, tenant_id)
    if loaded_attachments:
        # ADR-004 addendum / ADR-008: the attached image(s) this turn's
        # HumanMessage actually carried are real evidence too - the
        # responding model directly examined them, same as it examines
        # retrieved contract text. Without this, Output Guard's grounding
        # check has literally nothing to validate an image-describing claim
        # against and incorrectly rejects a genuine, correct vision answer
        # as insufficient_scope (confirmed live). image_attachment is
        # deliberately not one of the source types that satisfies the
        # separate legal-terms/text-evidence check below, so a turn mixing
        # an image with real contract claims still needs real contract
        # evidence for those claims, unchanged.
        evidence_envelope["evidence"] = evidence_envelope.get("evidence", []) + [
            image_attachment_evidence_item(loaded["attachment_id"], tenant_id, loaded["mime_type"])
            for loaded in loaded_attachments
        ]
    context_metadata["evidence_envelope"] = evidence_envelope
    logger.info("Contract Chat evidence prepared: %s", evidence_summary(evidence_envelope))
    deterministic_metadata_answer = render_deterministic_metadata_answer(
        prompt,
        evidence_envelope,
    )
    if deterministic_metadata_answer is not None:
        ai_full_content = deterministic_metadata_answer
        logger.info("Using deterministic metadata answer from validated evidence")

    # UX signal only, not a state transition of its own: generation has
    # finished (nothing left to stream) but the answer isn't done yet -
    # Output Guard's audit step is about to run and can legitimately take
    # several, sometimes tens of, seconds (bounded by
    # OUTPUT_GUARD_TIMEOUT_SECONDS/AUDIT_ATTEMPT_TIMEOUT_SECONDS below).
    # Without this, that wait looked identical to a stuck request. No retry
    # internals are exposed to the client - just "generating" vs "checking
    # the answer" as two distinct, visible phases.
    yield f"data: {json.dumps({'content': '', 'type': 'status', 'phase': 'verifying'})}\n\n"

    output_guard = OutputGuard(audit_logger=audit_logger, model_manager=llm_mgr)
    post_check_result = await _validate_output_guard(
        output_guard,
        ai_full_content,
        context_metadata,
    )
    output_status = _guard_status(post_check_result)

    # Agentic self-correction: a CONTRADICTED_OUTPUT/HALLUCINATION_DETECTED
    # verdict carries the judge's own step-by-step reasoning (hallucination.
    # py's CoT "reasoning" field) - real, actionable feedback, not just a
    # pass/fail bit. Retrying asks the generator to revise its own draft
    # against the SAME already-retrieved evidence plus that feedback; it
    # never re-runs tools, never sees new evidence, and the revision is
    # re-validated by Output Guard from scratch every time - this widens
    # what a genuinely correct answer looks like, it does not weaken what
    # counts as passing. Deliberately excludes every other rejection reason
    # (missing/cross-tenant/fabricated evidence, infrastructure failures,
    # timeouts, deterministic pre-checks) - none of those are fixable by
    # asking the same model to phrase the same evidence differently, and a
    # deterministic metadata answer (below) is correct by construction, not
    # a candidate for revision.
    regeneration_attempt = 0
    while (
        output_status != GuardStatus.PASSED
        and post_check_result.violation_type in {"CONTRADICTED_OUTPUT", "HALLUCINATION_DETECTED"}
        and regeneration_attempt < MAX_GENERATION_RETRIES
        and deterministic_metadata_answer is None
    ):
        regeneration_attempt += 1
        judge_reasoning = (post_check_result.metadata or {}).get("reasoning")
        logger.warning(
            "Output Guard rejected draft (%s) on generation attempt %d/%d; "
            "asking the generator to revise against the same evidence",
            post_check_result.violation_type, regeneration_attempt, MAX_GENERATION_RETRIES,
        )
        try:
            raw_model = llm_mgr.get_raw_model_by_name(model)
            revision_prompt = (
                "You are revising your own previous answer to a Contract Chat question. "
                "A safety reviewer rejected your previous draft because it made a claim "
                "not supported by the evidence you were given.\n\n"
                f"Reviewer feedback: {judge_reasoning or 'The draft contained an unsupported or contradicted claim.'}\n\n"
                "Rewrite your answer using ONLY the evidence envelope below. Remove or correct "
                "any claim the reviewer flagged. If, after re-reading the evidence, no part of "
                "the question can be answered, respond with exactly: 'The specific clause was "
                "not found in the documents.'\n\n"
                f"<EVIDENCE_ENVELOPE>\n{json.dumps(evidence_envelope, sort_keys=True, default=str)}\n</EVIDENCE_ENVELOPE>\n\n"
                f"<YOUR_PREVIOUS_DRAFT>\n{ai_full_content}\n</YOUR_PREVIOUS_DRAFT>\n"
            )
            revised = await raw_model.ainvoke(revision_prompt)
            revised_text = _normalize_ai_message_content(getattr(revised, "content", revised))
        except Exception as exc:
            logger.error(f"Self-correction regeneration failed ({type(exc).__name__}); keeping rejection")
            break
        if not revised_text or not revised_text.strip():
            break
        ai_full_content = revised_text
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
        # ADR-004 addendum: HallucinationValidator's audit-retry telemetry,
        # surfaced regardless of aggregation tie-breaking (base.py's
        # _aggregate_validator_results) - None when this turn's evidence was
        # rejected before the audit step ever ran (e.g. missing evidence),
        # since only the audit judgment call itself is retried.
        audit_attempts=(post_check_result.metadata or {}).get("audit_attempts"),
        audit_retry_used=(post_check_result.metadata or {}).get("audit_retry_used"),
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


def _saw_end_event(event: str) -> bool:
    try:
        payload = json.loads(event.removeprefix("data: ").strip())
    except (TypeError, ValueError):
        return False
    return payload.get("type") == "end"


def _fallback_terminal_status(run: Optional[ActiveChatRun]) -> tuple[str, str, str]:
    """(status, reason_category, message) for a fallback terminal event
    when `source` ended without ever yielding one of its own.

    `run` is cancellable_chat_stream's own ActiveChatRun - its `outcome`
    field is set in cancellable_chat_stream's own `finally` block, which
    (per Python generator semantics) always finishes running before this
    function's `async for` can observe the stream ending, cancelled or
    not. Reading it here is label-only: it picks an accurate status for
    a fallback event that was going to be sent regardless, never a second
    source of cancellation logic, and never consulted for anything other
    than which label to use.
    """
    if run is not None and run.outcome == "cancelled":
        return "cancelled", "client_cancellation", "Generation stopped."
    return "generation_failed", "generation_failed", "Response failed before completion. Please retry."


def _terminal_event_pair(status: str, reason_category: str, message: str) -> tuple[str, str]:
    return (
        f"data: {json.dumps({'content': message, 'type': 'error', 'status': status, 'reason_category': reason_category})}\n\n",
        f"data: {json.dumps({'content': '', 'type': 'end', 'status': status, 'reason_category': reason_category})}\n\n",
    )


async def _guaranteed_terminal_stream(
    source: AsyncIterator[str],
    run: Optional[ActiveChatRun] = None,
) -> AsyncIterator[str]:
    """Defense-in-depth around the whole SSE pipeline (resilient_runner,
    cancellable_chat_stream, or any future wrapper): regardless of how or
    why `source` ends - a clean finish, an ordinary exception, or a
    CancelledError from ANY cause - the client is guaranteed to receive one
    well-formed terminal 'end' event before the stream closes.

    Real, confirmed bug found live: a spurious mid-generation
    asyncio.CancelledError occurred with no explicit /api/chat/runs/.../
    cancel call ever made (confirmed via full log search - see ADR-004's
    sibling investigation notes in the task record). resilient_runner's own
    CancelledError handler persists server-side state and re-raises with no
    client-facing event at all; cancellable_chat_stream's cancellation-race
    loop only catches StopAsyncIteration around next_event.result(), so any
    other exception (including this one) propagated out uncaught. The
    browser's SSE connection was left with nothing further ever arriving -
    reproduced live as a stall exceeding 400 seconds, well past every
    configured timeout, because none of those timeouts are the right layer
    to catch "the client was never told the stream ended" at all.

    Live-verified separately: cancellable_chat_stream's OWN deliberate
    cancellation-race branch (an explicit Stop Generating click) ends its
    generator via a plain `break` - ordinary StopAsyncIteration, no
    exception at all - which first surfaced as this fallback mislabeling a
    genuine cancellation as "generation_failed". `run` closes that: passed
    only when wrapping cancellable_chat_stream, ignored otherwise.

    This is deliberately the single, outermost point of guarantee - fixing
    each individual internal call site that could raise would only cover
    known causes; this covers the whole class, including causes not yet
    found (see the addendum's item 3 investigation into this exact
    incident's spurious trigger).
    """
    saw_end = False
    try:
        async for event in source:
            if _saw_end_event(event):
                saw_end = True
            yield event
    except asyncio.CancelledError:
        if not saw_end:
            for fallback_event in _terminal_event_pair("cancelled", "client_cancellation", "Generation stopped."):
                yield fallback_event
        return
    except Exception:
        logger.error(
            "Chat stream ended on an unexpected exception past every inner "
            "handler; a fallback terminal event was sent so the client "
            "never hangs",
            exc_info=True,
        )
        if not saw_end:
            for fallback_event in _terminal_event_pair(*_fallback_terminal_status(run)):
                yield fallback_event
        return
    if not saw_end:
        # The stream finished (StopAsyncIteration) without ever yielding a
        # terminal event - either a deliberate cancellation via a plain
        # `break` (see the docstring above) or, for any other cause, the
        # backstop for resilient_runner's own contract being violated by a
        # future change.
        logger.error("Chat stream ended without a terminal event; a fallback was sent")
        for fallback_event in _terminal_event_pair(*_fallback_terminal_status(run)):
            yield fallback_event


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
    # Phase 6: payload.model == "auto" is resolved to a concrete registry
    # id (student for simple extraction, teacher for complex synthesis/
    # redline prompts) before validate_model ever sees it - routing always
    # lands on an id validate_model/llm_mgr already know how to handle, the
    # exact same path a manually-selected model takes from here down.
    resolved_model = payload.model
    if resolved_model == AUTO_MODEL_ID:
        resolved_model, _ = route_chat_model(payload.prompt)

    try:
        selected_spec = validate_model(resolved_model, "chat")
    except ModelSelectionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "category": exc.category},
        )
    if resolved_model not in llm_mgr.agents:
        raise HTTPException(status_code=503, detail="Selected model is temporarily unavailable")
    if payload.run_id and not payload.session_id:
        raise HTTPException(status_code=400, detail="Cancellable chat runs require a session")
    effective_contract_id = normalize_contract_scope(payload.contract_id)
    if payload.attachment_ids and not payload.session_id:
        raise HTTPException(status_code=400, detail="Image attachments require an active chat session")
    if payload.session_id:
        # Ownership check happens here, not inside runner(): runner() is an
        # async generator feeding StreamingResponse, and by the time it
        # could raise, response headers (200, text/event-stream) may
        # already be committed, so a clean HTTPException(404) isn't
        # reliably achievable from inside it. This is a plain coroutine.
        session_repo = Neo4jChatSessionRepository()
        session = session_repo.get_session(payload.session_id, identity.tenant_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {payload.session_id} not found")
        persisted_contract_id = normalize_contract_scope(session.get("contract_id"))
        if effective_contract_id != persisted_contract_id:
            # Reject before StreamingResponse starts and before runner()
            # appends the user turn, so the stored conversation remains
            # unchanged after an attempted scope override.
            raise HTTPException(status_code=409, detail="Contract scope does not match chat session")
        effective_contract_id = persisted_contract_id

        if payload.attachment_ids:
            if len(payload.attachment_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
                raise HTTPException(
                    status_code=400,
                    detail=f"Too many attachments (max {MAX_ATTACHMENTS_PER_MESSAGE} per message)",
                )
            # Explicit vision-capability gate (ADR-008): reject before any
            # provider call, reusing model_registry.py's existing
            # capabilities data - no silent fallback/degrade for a model
            # that can't actually see the image.
            if "vision" not in selected_spec.capabilities:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "Selected model does not support image attachments",
                        "category": "vision_unsupported",
                    },
                )
            # Same "clean 400 before streaming starts" reasoning as the
            # session/contract checks above - each attachment must already
            # exist and belong to this exact tenant+session.
            for attachment_id in payload.attachment_ids:
                if not session_repo.get_attachment(attachment_id, identity.tenant_id, payload.session_id):
                    raise HTTPException(status_code=404, detail=f"Attachment {attachment_id} not found")

    if effective_contract_id and not contract_exists_for_tenant(
        effective_contract_id, identity.tenant_id
    ):
        # Same response for a missing contract and one owned by another
        # tenant; the tenant predicate is inside the Neo4j query.
        raise HTTPException(status_code=404, detail="Contract not found")

    runner_kwargs = {
        "model": resolved_model,
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
        "attachment_ids": payload.attachment_ids,
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
        guarded_stream = _guaranteed_terminal_stream(stream, run=active_run)
    else:
        stream = resilient_runner(**runner_kwargs)
        guarded_stream = _guaranteed_terminal_stream(stream)

    return StreamingResponse(guarded_stream, media_type="text/event-stream")
