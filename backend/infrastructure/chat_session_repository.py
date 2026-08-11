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
	   the LLM's own context, which LangChain's ToolMessage requires),
	   terminal_status (nullable, assistant terminal outcome), created_at})
  (:ChatSession)-[:HAS_MESSAGE {sequence: int}]->(:ChatMessage)
  (:ChatAttachment {attachment_id, tenant_id, session_id, mime_type,
   size_bytes, storage_key, created_at}) - Contract Chat image attachments
   (ADR-008). Bytes live encrypted on disk (chat_attachment_storage.py);
   this node is metadata only, never the image content. tenant_id AND
   session_id are both node properties (not solely derivable from a
   relationship) for the same isolation-via-property-filter reason
   ChatSession/ChatMessage use them - a cross-tenant/cross-session read is
   rejected by the MATCH predicate itself, not by a relationship traversal.
  (:ChatMessage)-[:HAS_ATTACHMENT]->(:ChatAttachment) - mirrors
   (:Document)-[:HAS_CHUNK]->(:Chunk)'s existing convention. An attachment
   is created (via create_attachment) before the message that will
   reference it exists - the two-phase upload-then-send flow chat images
   need - and linked once that message is actually persisted
   (link_attachment_to_message). An uploaded-but-never-sent attachment is a
   known, accepted orphan class, same as the PDF pipeline's own orphan case
   (docs/tasks/active/pdf-citations-model-selection.md's risks) - no
   retention/purge tooling is built here.

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
import json
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


def generate_attachment_id() -> str:
    return f"ATTACH_{uuid.uuid4().hex[:12].upper()}"


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
            WHERE s.archived_at IS NULL
            RETURN s.session_id AS session_id, s.contract_id AS contract_id, s.title AS title,
                   s.created_at AS created_at, s.updated_at AS updated_at, s.message_count AS message_count
            ORDER BY s.updated_at DESC
            """
            params = {"tenant_id": tenant_id}
        else:
            cypher = """
            MATCH (s:ChatSession {tenant_id: $tenant_id, contract_id: $contract_id})
            WHERE s.archived_at IS NULL
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
        WHERE s.archived_at IS NULL
        RETURN s.session_id AS session_id, s.tenant_id AS tenant_id, s.contract_id AS contract_id,
               s.title AS title, s.created_at AS created_at, s.updated_at AS updated_at,
               s.message_count AS message_count
        """
        result = self._query(cypher, {"session_id": session_id, "tenant_id": tenant_id})
        return result[0] if result else None

    def rename_session(self, session_id: str, tenant_id: str, title: str) -> Optional[Dict[str, Any]]:
        cypher = """
        MATCH (s:ChatSession {session_id: $session_id, tenant_id: $tenant_id})
        WHERE s.archived_at IS NULL
        SET s.title = $title, s.updated_at = datetime()
        RETURN s.session_id AS session_id, s.tenant_id AS tenant_id, s.contract_id AS contract_id,
               s.title AS title, s.created_at AS created_at, s.updated_at AS updated_at,
               s.message_count AS message_count
        """
        rows = self._query(cypher, {"session_id": session_id, "tenant_id": tenant_id, "title": title})
        return rows[0] if rows else None

    def list_messages(self, session_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        """Empty list for a missing/cross-tenant session - the MATCH's
        tenant_id filter makes a cross-tenant read naturally return
        nothing, same defense-in-depth posture as every other tenant-scoped
        read in this codebase.

        has_attachment (OPTIONAL MATCH + count(...) > 0) tells the caller
        whether a user_message row had an image linked, without a second
        per-row query - main.py's _messages_from_stored uses it to tell the
        model an earlier turn's image is no longer available in the
        current turn's context (ADR-008 multi-turn image-context fix),
        since content itself never carries the image (see
        link_attachment_to_message's docstring)."""
        cypher = """
        MATCH (s:ChatSession {session_id: $session_id, tenant_id: $tenant_id})-[r:HAS_MESSAGE]->(m:ChatMessage)
        WHERE s.archived_at IS NULL
        OPTIONAL MATCH (m)-[:HAS_ATTACHMENT]->(a:ChatAttachment)
        WITH s, r, m, count(a) AS attachment_count
        RETURN m.message_id AS message_id, m.role AS role, m.content AS content, m.model AS model,
               m.requested_model AS requested_model, m.actual_model AS actual_model,
               m.requested_provider AS requested_provider, m.actual_provider AS actual_provider,
               m.fallback_occurred AS fallback_occurred, m.fallback_reason AS fallback_reason,
               m.prompt_version AS prompt_version, m.execution_path AS execution_path,
               m.tool_name AS tool_name, m.tool_call_id AS tool_call_id, m.citations AS citations,
               m.terminal_status AS terminal_status, m.terminal_reason AS terminal_reason,
               m.created_at AS created_at, attachment_count > 0 AS has_attachment,
               r.sequence AS sequence
        ORDER BY r.sequence ASC
        """
        rows = self._query(cypher, {"session_id": session_id, "tenant_id": tenant_id})
        for row in rows:
            row["content"] = field_encryptor.decrypt(row["content"] or "")
            encrypted_citations = row.get("citations")
            if encrypted_citations:
                try:
                    row["citations"] = json.loads(field_encryptor.decrypt(encrypted_citations))
                except (TypeError, ValueError, json.JSONDecodeError):
                    logger.warning("Ignoring unreadable chat citation metadata")
                    row["citations"] = []
            else:
                row["citations"] = []
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
        citations: Optional[List[Dict[str, Any]]] = None,
        terminal_status: Optional[str] = None,
        terminal_reason: Optional[str] = None,
        requested_model: Optional[str] = None,
        actual_model: Optional[str] = None,
        requested_provider: Optional[str] = None,
        actual_provider: Optional[str] = None,
        fallback_occurred: bool = False,
        fallback_reason: Optional[str] = None,
        prompt_version: Optional[str] = None,
        execution_path: Optional[str] = None,
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
        WHERE s.archived_at IS NULL
        SET s.message_count = coalesce(s.message_count, 0) + 1,
            s.updated_at = datetime()
        WITH s, s.message_count AS seq
        CREATE (m:ChatMessage {
            message_id: $message_id, tenant_id: $tenant_id, role: $role, content: $content,
            model: $model, tool_name: $tool_name, tool_call_id: $tool_call_id,
            requested_model: $requested_model, actual_model: $actual_model,
            requested_provider: $requested_provider, actual_provider: $actual_provider,
            fallback_occurred: $fallback_occurred, fallback_reason: $fallback_reason,
            prompt_version: $prompt_version, execution_path: $execution_path,
            citations: $citations, terminal_status: $terminal_status,
            terminal_reason: $terminal_reason,
            created_at: datetime()
        })
        CREATE (s)-[:HAS_MESSAGE {sequence: seq}]->(m)
        RETURN m.message_id AS message_id, seq AS sequence, m.created_at AS created_at
        """
        result = self._query(cypher, {
            "session_id": session_id, "tenant_id": tenant_id, "message_id": message_id,
            "role": role, "content": field_encryptor.encrypt(content or ""),
            "model": model, "tool_name": tool_name, "tool_call_id": tool_call_id,
            "citations": field_encryptor.encrypt(json.dumps(citations, default=str)) if citations else None,
            "terminal_status": terminal_status,
            "terminal_reason": terminal_reason,
            "requested_model": requested_model,
            "actual_model": actual_model,
            "requested_provider": requested_provider,
            "actual_provider": actual_provider,
            "fallback_occurred": fallback_occurred,
            "fallback_reason": fallback_reason,
            "prompt_version": prompt_version,
            "execution_path": execution_path,
        })
        return result[0] if result else None

    def create_attachment(
        self,
        session_id: str,
        tenant_id: str,
        attachment_id: str,
        mime_type: str,
        size_bytes: int,
        storage_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Creates a standalone ChatAttachment, not yet linked to any
        message - the upload step of the two-phase upload-then-send flow.
        MATCH is tenant_id-scoped (same posture as every other write path
        here): a caller can never attach to another tenant's session even
        if it somehow obtained the session_id.

        attachment_id is caller-generated (generate_attachment_id()), not
        minted inside this method: the storage layer's storage_key is a
        pure function of (tenant_id, session_id, attachment_id), so the
        real image bytes must already be encrypted and on disk - keyed off
        this same id - before this graph write happens. Writing the graph
        row first would leave a moment where a node claims a storage_key
        that doesn't exist yet."""
        cypher = """
        MATCH (s:ChatSession {session_id: $session_id, tenant_id: $tenant_id})
        WHERE s.archived_at IS NULL
        CREATE (a:ChatAttachment {
            attachment_id: $attachment_id, tenant_id: $tenant_id, session_id: $session_id,
            mime_type: $mime_type, size_bytes: $size_bytes, storage_key: $storage_key,
            created_at: datetime()
        })
        RETURN a.attachment_id AS attachment_id, a.tenant_id AS tenant_id,
               a.session_id AS session_id, a.mime_type AS mime_type,
               a.size_bytes AS size_bytes, a.storage_key AS storage_key,
               a.created_at AS created_at
        """
        result = self._query(cypher, {
            "session_id": session_id, "tenant_id": tenant_id, "attachment_id": attachment_id,
            "mime_type": mime_type, "size_bytes": size_bytes, "storage_key": storage_key,
        })
        return result[0] if result else None

    def get_attachment(self, attachment_id: str, tenant_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Ownership-checked metadata fetch for the retrieval endpoint -
        None for missing, cross-tenant, AND cross-session (an attachment
        genuinely owned by this tenant but a different one of their own
        sessions is still rejected; the URL path's session_id must match
        exactly), matching get_session's indistinguishable-404 posture."""
        cypher = """
        MATCH (a:ChatAttachment {attachment_id: $attachment_id, tenant_id: $tenant_id, session_id: $session_id})
        RETURN a.attachment_id AS attachment_id, a.tenant_id AS tenant_id,
               a.session_id AS session_id, a.mime_type AS mime_type,
               a.size_bytes AS size_bytes, a.storage_key AS storage_key,
               a.created_at AS created_at
        """
        result = self._query(cypher, {
            "attachment_id": attachment_id, "tenant_id": tenant_id, "session_id": session_id,
        })
        return result[0] if result else None

    def link_attachment_to_message(self, attachment_id: str, message_id: str, tenant_id: str) -> bool:
        """Links a previously-uploaded attachment to the message that was
        just persisted for the turn it belongs to. Both MATCHes are
        tenant_id-scoped; returns False (a safe no-op) if either the
        attachment or the message doesn't exist for this tenant - a caller
        can never link across tenants even by guessing IDs."""
        cypher = """
        MATCH (a:ChatAttachment {attachment_id: $attachment_id, tenant_id: $tenant_id})
        MATCH (m:ChatMessage {message_id: $message_id, tenant_id: $tenant_id})
        CREATE (m)-[:HAS_ATTACHMENT]->(a)
        RETURN a.attachment_id AS attachment_id
        """
        result = self._query(cypher, {
            "attachment_id": attachment_id, "message_id": message_id, "tenant_id": tenant_id,
        })
        return bool(result)

    def list_attachments_for_message(self, message_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        """Empty list for a missing/cross-tenant message - same defense-in-
        depth posture as list_messages. Used to restore attachment
        references onto a message on session load."""
        cypher = """
        MATCH (m:ChatMessage {message_id: $message_id, tenant_id: $tenant_id})-[:HAS_ATTACHMENT]->(a:ChatAttachment)
        RETURN a.attachment_id AS attachment_id, a.mime_type AS mime_type,
               a.size_bytes AS size_bytes, a.created_at AS created_at
        ORDER BY a.created_at ASC
        """
        return self._query(cypher, {"message_id": message_id, "tenant_id": tenant_id})
