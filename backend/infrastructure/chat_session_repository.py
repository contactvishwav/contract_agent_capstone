"""
Persistent Contract Chat sessions. Previously, conversation history lived
entirely in a frontend useRef (frontend/src/components/features/contracts/
input.tsx) - lost on navigating away, on refresh, and on logout/login, and
in practice lost even sooner than that: nothing durably existed until the
client round-tripped the SSE "history" event back on the *next* prompt, so
a refresh immediately after receiving an answer lost that answer before the
user ever left the page.

Schema:
  (:ChatSession {session_id, tenant_id, contract_id (OMITTED for "All
   Contracts" - Neo4j never stores a null property, and this repository
   never treats contract_id=None as "filter for null"), title, created_at,
   updated_at, message_count})
  (:ChatMessage {message_id, tenant_id, role, content (encrypted at rest,
   same as Contract.full_text/Clause.content/Chunk.content), model
   (nullable, "ai_message" rows only - forward-compat hook for future
   per-message model attribution), tool_name (nullable, "tool_call" rows),
   tool_call_id (nullable, "tool_call"/"tool_message" rows - forward-compat
   hook so a tool_call/tool_message pair can later be replayed back into
   the LLM's own context, which LangChain's ToolMessage requires), created_at})
  (:ChatSession)-[:HAS_MESSAGE {sequence: int}]->(:ChatMessage)

role uses four values - "user_message" | "ai_message" | "tool_call" |
"tool_message" - deliberately matching frontend/src/components/features/
contracts/provider.tsx's MessagePart.type exactly, so a restored session
replays into the existing frontend rendering logic with zero translation
layer.

Deliberately no (:Contract)-[:HAS_CHAT_SESSION]->(:ChatSession) relationship:
every session (including All-Contracts ones, which have no Contract node to
link to) already needs tenant_id/contract_id as *properties* for isolation
and listing, so a parallel relationship would be a second, driftable source
of the same fact with no isolation role - isolation in this codebase is
always enforced via property filters in WHERE/MATCH, never via relationship
traversal alone.
"""

from typing import Any, Dict, List, Optional
import uuid

from backend.infrastructure.encryption import field_encryptor
from backend.shared.reliability.circuit_breaker import NEO4J_CIRCUIT_BREAKER
from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


def generate_session_id() -> str:
    return f"SESSION_{uuid.uuid4().hex[:12].upper()}"


def generate_message_id() -> str:
    return f"MSG_{uuid.uuid4().hex[:12].upper()}"


class Neo4jChatSessionRepository:
    def __init__(self):
        self.graph = graph  # Reuse existing connection, same as every other repository

    def _query(self, cypher: str, params: Optional[Dict[str, Any]] = None):
        with NEO4J_CIRCUIT_BREAKER.guard():
            return self.graph.query(cypher, params)

    def create_session(self, tenant_id: str, contract_id: Optional[str], title: str) -> Dict[str, Any]:
        session_id = generate_session_id()
        cypher = """
        CREATE (s:ChatSession {
            session_id: $session_id, tenant_id: $tenant_id, contract_id: $contract_id,
            title: $title, created_at: datetime(), updated_at: datetime(), message_count: 0
        })
        RETURN s.session_id AS session_id, s.tenant_id AS tenant_id, s.contract_id AS contract_id,
               s.title AS title, s.created_at AS created_at, s.updated_at AS updated_at,
               s.message_count AS message_count
        """
        # contract_id=None is simply omitted from the created node (Neo4j
        # never stores a null property) - this IS the "All Contracts"
        # representation, not a bug to work around.
        result = self._query(cypher, {
            "session_id": session_id, "tenant_id": tenant_id,
            "contract_id": contract_id, "title": title,
        })
        return result[0]

    def list_sessions(self, tenant_id: str, contract_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """contract_id=None means "no filter" (the flat switcher's default
        view), NOT "filter for sessions with no contract" - Cypher's
        {prop: $x}/`= $x` never matches when $x is null (three-valued
        logic), and this feature never needs an explicit
        All-Contracts-only query, so that branch is deliberately not
        offered here."""
        if contract_id is None:
            cypher = """
            MATCH (s:ChatSession {tenant_id: $tenant_id})
            RETURN s.session_id AS session_id, s.contract_id AS contract_id, s.title AS title,
                   s.created_at AS created_at, s.updated_at AS updated_at, s.message_count AS message_count
            ORDER BY s.updated_at DESC
            """
            params = {"tenant_id": tenant_id}
        else:
            cypher = """
            MATCH (s:ChatSession {tenant_id: $tenant_id, contract_id: $contract_id})
            RETURN s.session_id AS session_id, s.contract_id AS contract_id, s.title AS title,
                   s.created_at AS created_at, s.updated_at AS updated_at, s.message_count AS message_count
            ORDER BY s.updated_at DESC
            """
            params = {"tenant_id": tenant_id, "contract_id": contract_id}
        return self._query(cypher, params)

    def get_session(self, session_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Ownership-checked metadata fetch. Returns None for both "doesn't
        exist" and "belongs to a different tenant" - indistinguishable,
        matching contract_intelligence.py's cross-tenant 404 pattern, so a
        caller never learns whether a session_id it doesn't own exists at
        all."""
        cypher = """
        MATCH (s:ChatSession {session_id: $session_id, tenant_id: $tenant_id})
        RETURN s.session_id AS session_id, s.tenant_id AS tenant_id, s.contract_id AS contract_id,
               s.title AS title, s.created_at AS created_at, s.updated_at AS updated_at,
               s.message_count AS message_count
        """
        result = self._query(cypher, {"session_id": session_id, "tenant_id": tenant_id})
        return result[0] if result else None

    def list_messages(self, session_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        """Empty list for a missing/cross-tenant session - the MATCH's
        tenant_id filter makes a cross-tenant read naturally return
        nothing, same defense-in-depth posture as every other tenant-scoped
        read in this codebase."""
        cypher = """
        MATCH (s:ChatSession {session_id: $session_id, tenant_id: $tenant_id})-[r:HAS_MESSAGE]->(m:ChatMessage)
        RETURN m.message_id AS message_id, m.role AS role, m.content AS content, m.model AS model,
               m.tool_name AS tool_name, m.tool_call_id AS tool_call_id, m.created_at AS created_at,
               r.sequence AS sequence
        ORDER BY r.sequence ASC
        """
        rows = self._query(cypher, {"session_id": session_id, "tenant_id": tenant_id})
        for row in rows:
            row["content"] = field_encryptor.decrypt(row["content"] or "")
        return rows

    def append_message(
        self,
        session_id: str,
        tenant_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """MATCH is tenant_id-scoped, same as every other write path in this
        codebase - a caller can never append to another tenant's session
        even if it somehow obtained the session_id; the MATCH simply finds
        nothing and this returns None, treated as an error by the caller.

        message_count increment, sequence assignment, and message creation
        all happen in one Cypher statement/transaction - Neo4j's implicit
        write lock on `s` from the SET serializes concurrent appends to the
        same session, so there is no lost-update race between reading the
        count and creating the relationship."""
        message_id = generate_message_id()
        cypher = """
        MATCH (s:ChatSession {session_id: $session_id, tenant_id: $tenant_id})
        SET s.message_count = coalesce(s.message_count, 0) + 1,
            s.updated_at = datetime()
        WITH s, s.message_count AS seq
        CREATE (m:ChatMessage {
            message_id: $message_id, tenant_id: $tenant_id, role: $role, content: $content,
            model: $model, tool_name: $tool_name, tool_call_id: $tool_call_id, created_at: datetime()
        })
        CREATE (s)-[:HAS_MESSAGE {sequence: seq}]->(m)
        RETURN m.message_id AS message_id, seq AS sequence, m.created_at AS created_at
        """
        result = self._query(cypher, {
            "session_id": session_id, "tenant_id": tenant_id, "message_id": message_id,
            "role": role, "content": field_encryptor.encrypt(content or ""),
            "model": model, "tool_name": tool_name, "tool_call_id": tool_call_id,
        })
        return result[0] if result else None
