"""Provider-neutral, tenant-validated citations for Contract Chat.

Citations are derived only from tool results produced during the current
turn.  Stored citation metadata is never trusted on replay: every contract
reference is checked again against the authenticated tenant and active
lifecycle before it can leave the API.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Optional

from backend.shared.utils.contract_search_tool import graph

MAX_EXCERPT_LENGTH = 500


def _parse_tool_content(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(content)
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
    return None


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_mappings(child)


def _source_type(item: Mapping[str, Any]) -> Optional[str]:
    if item.get("chunk_id") is not None or item.get("chunk_index") is not None:
        return "chunk"
    if item.get("clause_id") is not None or item.get("clause_type") is not None:
        return "clause"
    if item.get("section_id") is not None or item.get("section_type") is not None:
        return "section"
    if item.get("party_name") is not None or item.get("role") is not None and item.get("context") is not None:
        return "relationship"
    if item.get("file_id") is not None or item.get("contract_id") is not None:
        return "document"
    return None


def _excerpt(item: Mapping[str, Any]) -> Optional[str]:
    for key in ("content", "context", "summary", "text", "snippet"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:MAX_EXCERPT_LENGTH]
    return None


def _active_contracts(tenant_id: str, contract_ids: list[str], graph_client: Any) -> dict[str, str]:
    if not contract_ids:
        return {}
    rows = graph_client.query(
        """
        MATCH (c:Contract)
        WHERE c.tenant_id = $tenant_id
          AND c.file_id IN $contract_ids
          AND coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
        RETURN c.file_id AS contract_id, c.filename AS filename
        """,
        {"tenant_id": tenant_id, "contract_ids": sorted(set(contract_ids))},
    )
    return {str(row["contract_id"]): str(row.get("filename") or row["contract_id"]) for row in rows}


def build_validated_citations(
    tool_messages: Iterable[Mapping[str, Any]],
    tenant_id: str,
    *,
    graph_client: Any = graph,
) -> list[dict[str, Any]]:
    """Build citations from this turn's tool evidence and validate ownership."""
    candidates: list[dict[str, Any]] = []
    for tool_message in tool_messages:
        parsed = _parse_tool_content(tool_message.get("content"))
        for item in _walk_mappings(parsed):
            contract_id = item.get("contract_id") or item.get("file_id")
            source_type = _source_type(item)
            if not contract_id or not source_type:
                continue
            candidates.append(
                {
                    "contract_id": str(contract_id),
                    "filename": item.get("filename"),
                    "source_type": source_type,
                    "page": item.get("page") or item.get("page_number"),
                    "section_id": item.get("section_id"),
                    "section_title": item.get("section_title") or item.get("section_type"),
                    "clause_id": item.get("clause_id"),
                    "clause_type": item.get("clause_type"),
                    "chunk_id": item.get("chunk_id"),
                    "chunk_index": item.get("chunk_index"),
                    "start_offset": item.get("start_offset") or item.get("start_position"),
                    "end_offset": item.get("end_offset") or item.get("end_position"),
                    "excerpt": _excerpt(item),
                    "tool_name": tool_message.get("tool_name"),
                    "tool_call_id": tool_message.get("tool_call_id"),
                }
            )

    active = _active_contracts(tenant_id, [c["contract_id"] for c in candidates], graph_client)
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        filename = active.get(candidate["contract_id"])
        if filename is None:
            continue
        candidate["filename"] = filename
        candidate["validation_status"] = "tenant_active"
        identity = json.dumps(candidate, sort_keys=True, default=str)
        citation_id = f"CIT_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16].upper()}"
        if citation_id in seen:
            continue
        seen.add(citation_id)
        citations.append({"citation_id": citation_id, **candidate})
    return citations


def revalidate_stored_citations(
    citations: Any,
    tenant_id: str,
    *,
    graph_client: Any = graph,
) -> list[dict[str, Any]]:
    """Re-check stored citations; never trust their saved validation flag."""
    if not isinstance(citations, list):
        return []
    active = _active_contracts(
        tenant_id,
        [str(c.get("contract_id")) for c in citations if isinstance(c, Mapping) and c.get("contract_id")],
        graph_client,
    )
    validated = []
    for citation in citations:
        if not isinstance(citation, Mapping):
            continue
        contract_id = str(citation.get("contract_id") or "")
        if contract_id not in active:
            continue
        safe = dict(citation)
        safe["filename"] = active[contract_id]
        safe["validation_status"] = "tenant_active"
        validated.append(safe)
    return validated
