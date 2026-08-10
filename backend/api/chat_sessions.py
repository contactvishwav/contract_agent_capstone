"""
Persistent Contract Chat sessions - list/create/detail routes. See
backend/infrastructure/chat_session_repository.py for the schema and
tenant-isolation reasoning this backs; backend/main.py's /api/run/ route
is where messages actually get appended to a session as a conversation
happens.
"""
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator

from backend.governance.auth import TokenIdentity
from backend.application.services.chat_citation_service import revalidate_stored_citations
from backend.governance.rbac import Permission, requires_permission
from backend.infrastructure.chat_attachment_storage import ChatAttachmentUnavailable, chat_attachment_storage
from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository, generate_attachment_id
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.middleware.rate_limit import CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT, limiter, tenant_scoped_or_ip_key
from backend.shared.utils.utils import serialize_neo4j_datetime

router = APIRouter(prefix="/api/chat", tags=["chat-sessions"])
repository = Neo4jChatSessionRepository()
contract_repository = Neo4jContractRepository()
ALL_CONTRACTS_SENTINEL = "__all_contracts__"

# 5MB: the tightest of the three configured providers' real per-image
# limits (Anthropic's) - see ADR-008. Going higher would mean Claude turns
# could get an explicit provider-side rejection that Gemini/GPT-4o
# wouldn't, an inconsistency deliberately avoided rather than accepted.
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024


def normalize_contract_scope(contract_id: Optional[str]) -> Optional[str]:
    """Canonical wire/storage representation for an All-Contracts scope."""
    if contract_id is None or contract_id == ALL_CONTRACTS_SENTINEL:
        return None
    normalized = contract_id.strip()
    return normalized or None


def contract_exists_for_tenant(contract_id: str, tenant_id: str) -> bool:
    """Verify ownership inside the Neo4j predicate, never after retrieval."""
    rows = contract_repository.graph.query(
        "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
        "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
        "RETURN c.file_id AS file_id",
        {"contract_id": contract_id, "tenant_id": tenant_id},
    )
    return bool(rows)


class CreateSessionRequest(BaseModel):
    contract_id: Optional[str] = None
    title: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("title must not be blank")
        if len(normalized) > 120:
            raise ValueError("title must be 120 characters or fewer")
        return normalized


class RenameSessionRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("title must not be blank")
        if len(normalized) > 120:
            raise ValueError("title must be 120 characters or fewer")
        return normalized


class SessionResponse(BaseModel):
    session_id: str
    contract_id: Optional[str] = None
    title: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0


class MessageResponse(BaseModel):
    message_id: str
    role: str
    content: str
    model: Optional[str] = None
    requested_model: Optional[str] = None
    actual_model: Optional[str] = None
    requested_provider: Optional[str] = None
    actual_provider: Optional[str] = None
    fallback_occurred: bool = False
    fallback_reason: Optional[str] = None
    prompt_version: Optional[str] = None
    execution_path: Optional[str] = None
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    citations: List[dict] = Field(default_factory=list)
    attachments: List[dict] = Field(default_factory=list)
    terminal_status: Optional[Literal[
        "passed",
        "rejected",
        "validation_failed",
        "timed_out",
        "cancelled",
        "empty",
        "generation_failed",
        "persistence_failed",
    ]] = None
    terminal_reason: Optional[str] = None
    sequence: int
    created_at: Optional[str] = None


class SessionDetailResponse(SessionResponse):
    messages: List[MessageResponse]


class AttachmentResponse(BaseModel):
    attachment_id: str
    mime_type: str
    size_bytes: int


def _serialize_session(row) -> SessionResponse:
    return SessionResponse(
        session_id=row["session_id"], contract_id=row.get("contract_id"), title=row["title"],
        created_at=serialize_neo4j_datetime(row.get("created_at")),
        updated_at=serialize_neo4j_datetime(row.get("updated_at")),
        message_count=row.get("message_count", 0),
    )


@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions(
    contract_id: Optional[str] = Query(None),
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    rows = repository.list_sessions(identity.tenant_id, contract_id)
    return [_serialize_session(r) for r in rows]


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    payload: CreateSessionRequest,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    contract_id = normalize_contract_scope(payload.contract_id)
    if contract_id:
        # Without this check, a client could scope a new session to a
        # contract_id belonging to a different tenant - it would then flow
        # into EnhancedContractSearchTool via config["configurable"][
        # "contract_id"] on every message sent in that session (see
        # backend/main.py's runner()), scoping searches against another
        # tenant's contract. Same "always validate ownership server-side"
        # posture as everywhere else contract_id is accepted from a client.
        if not contract_exists_for_tenant(contract_id, identity.tenant_id):
            raise HTTPException(status_code=404, detail=f"Contract {contract_id} not found")

    row = repository.create_session(identity.tenant_id, contract_id, payload.title or "New chat")
    return _serialize_session(row)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(
    session_id: str,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    session = repository.get_session(session_id, identity.tenant_id)
    if not session:
        # Same status for "doesn't exist" and "belongs to another tenant" -
        # never lets a caller distinguish the two, matching
        # contract_intelligence.py's existing cross-tenant 404 pattern.
        raise HTTPException(status_code=404, detail=f"Chat session {session_id} not found")

    messages = repository.list_messages(session_id, identity.tenant_id)
    return SessionDetailResponse(
        **_serialize_session(session).model_dump(),
        messages=[
            MessageResponse(
                message_id=m["message_id"], role=m["role"], content=m["content"],
                model=m.get("model"), tool_name=m.get("tool_name"),
                requested_model=m.get("requested_model"), actual_model=m.get("actual_model") or m.get("model"),
                requested_provider=m.get("requested_provider"), actual_provider=m.get("actual_provider"),
                fallback_occurred=bool(m.get("fallback_occurred")), fallback_reason=m.get("fallback_reason"),
                prompt_version=m.get("prompt_version"), execution_path=m.get("execution_path"),
                tool_call_id=m.get("tool_call_id"),
                citations=revalidate_stored_citations(
                    m.get("citations"), identity.tenant_id, answer_text=m.get("content"),
                ),
                attachments=(
                    # Only attachment_id/mime_type reach the client - not
                    # size_bytes/created_at (Neo4j datetime, not directly
                    # JSON-serializable, and not needed for rendering).
                    [
                        {"attachment_id": a["attachment_id"], "mime_type": a["mime_type"]}
                        for a in repository.list_attachments_for_message(m["message_id"], identity.tenant_id)
                    ] if m["role"] == "user_message" else []
                ),
                terminal_status=m.get("terminal_status"),
                terminal_reason=m.get("terminal_reason"),
                sequence=m["sequence"],
                created_at=serialize_neo4j_datetime(m.get("created_at")),
            )
            for m in messages
        ],
    )


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def rename_session(
    session_id: str,
    payload: RenameSessionRequest,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    row = repository.rename_session(session_id, identity.tenant_id, payload.title)
    if not row:
        raise HTTPException(status_code=404, detail=f"Chat session {session_id} not found")
    return _serialize_session(row)


@router.post("/sessions/{session_id}/attachments", response_model=AttachmentResponse, status_code=201)
@limiter.limit(CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT, key_func=tenant_scoped_or_ip_key)
async def upload_attachment(
    request: Request,
    session_id: str,
    file: UploadFile = File(...),
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Uploads one image into an existing session, ahead of the message
    that will reference it (see chat_session_repository.py's two-phase
    upload-then-send design note). Rate-limited per-tenant (ADR-008): a
    real disk-write operation reachable independent of /api/run/'s own
    limit. The `request: Request` parameter (unused directly here) is
    required by @limiter.limit, same convention as main.py's run().
    """
    session = repository.get_session(session_id, identity.tenant_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Chat session {session_id} not found")

    content = await file.read()
    if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Attachment too large (max 5MB)")

    # Encrypt-and-store first, keyed off a freshly generated id, before any
    # graph write - see create_attachment's docstring for why this order
    # matters. mime_type here is the real, sniffed format (never the
    # client-declared Content-Type); ValueError means the bytes aren't one
    # of the three supported image formats.
    attachment_id = generate_attachment_id()
    try:
        storage_key, mime_type = chat_attachment_storage.store(
            identity.tenant_id, session_id, attachment_id, content,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Attachment content is not a supported image format (PNG/JPEG/WEBP)")

    row = repository.create_attachment(
        session_id, identity.tenant_id, attachment_id, mime_type, len(content), storage_key,
    )
    if not row:
        # Session vanished (archived/deleted) between the get_session check
        # above and this write - roll back the now-orphaned encrypted blob
        # rather than leaving it unaddressable by any graph row.
        chat_attachment_storage.remove(identity.tenant_id, session_id, attachment_id)
        raise HTTPException(status_code=404, detail=f"Chat session {session_id} not found")

    return AttachmentResponse(attachment_id=attachment_id, mime_type=mime_type, size_bytes=len(content))


@router.get("/sessions/{session_id}/attachments/{attachment_id}")
async def get_attachment(
    session_id: str,
    attachment_id: str,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Returns one tenant/session-owned image attachment without exposing
    its storage path - same authenticated-retrieval shape as
    document_upload.py's get_contract_source_pdf."""
    attachment = repository.get_attachment(attachment_id, identity.tenant_id, session_id)
    if not attachment:
        # Missing, cross-tenant, and cross-session are deliberately
        # indistinguishable.
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        content = chat_attachment_storage.read(
            identity.tenant_id, session_id, attachment_id, attachment["storage_key"],
        )
    except ChatAttachmentUnavailable:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return Response(
        content=content,
        media_type=attachment["mime_type"],
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(attachment_id, safe='')}",
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
