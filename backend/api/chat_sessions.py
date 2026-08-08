"""
Persistent Contract Chat sessions - list/create/detail routes. See
backend/infrastructure/chat_session_repository.py for the schema and
tenant-isolation reasoning this backs; backend/main.py's /api/run/ route
is where messages actually get appended to a session as a conversation
happens.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.governance.auth import TokenIdentity
from backend.governance.rbac import Permission, requires_permission
from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.utils import serialize_neo4j_datetime

router = APIRouter(prefix="/api/chat", tags=["chat-sessions"])
repository = Neo4jChatSessionRepository()
contract_repository = Neo4jContractRepository()


class CreateSessionRequest(BaseModel):
    contract_id: Optional[str] = None
    title: Optional[str] = None


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
    tool_name: Optional[str] = None
    sequence: int
    created_at: Optional[str] = None


class SessionDetailResponse(SessionResponse):
    messages: List[MessageResponse]


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
    if payload.contract_id:
        # Without this check, a client could scope a new session to a
        # contract_id belonging to a different tenant - it would then flow
        # into EnhancedContractSearchTool via config["configurable"][
        # "contract_id"] on every message sent in that session (see
        # backend/main.py's runner()), scoping searches against another
        # tenant's contract. Same "always validate ownership server-side"
        # posture as everywhere else contract_id is accepted from a client.
        owns = contract_repository.graph.query(
            "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) RETURN c.file_id AS file_id",
            {"contract_id": payload.contract_id, "tenant_id": identity.tenant_id},
        )
        if not owns:
            raise HTTPException(status_code=404, detail=f"Contract {payload.contract_id} not found")

    row = repository.create_session(identity.tenant_id, payload.contract_id, payload.title or "New chat")
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
                model=m.get("model"), tool_name=m.get("tool_name"), sequence=m["sequence"],
                created_at=serialize_neo4j_datetime(m.get("created_at")),
            )
            for m in messages
        ],
    )
