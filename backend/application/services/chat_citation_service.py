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
from backend.application.services.pdf_provenance_service import (
    PdfProvenanceService,
    enrich_citations_with_provenance,
)
from backend.application.services.chat_evidence_service import (
    evidence_id,
    parse_evidence_envelope,
)

MAX_EXCERPT_LENGTH = 500


def _citation_source_identity(citation: Mapping[str, Any]) -> str:
    source = {
        key: value for key, value in citation.items()
        if key not in {
            "citation_id", "tool_name", "tool_call_id", "validation_status",
            # Derived from currently-authorized source metadata on every read;
            # never part of the caller/stored identity and never trusted on replay.
            "page", "highlight_text", "page_start_offset", "page_end_offset",
            "source_available", "provenance_status",
        }
    }
    return json.dumps(source, sort_keys=True, default=str)


def _citation_id(citation: Mapping[str, Any]) -> str:
    identity = _citation_source_identity(citation)
    return f"CIT_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16].upper()}"


def _legacy_citation_id(citation: Mapping[str, Any]) -> str:
    """Reproduce the pre-provenance identity for safe history migration.

    Older persisted citations included their (usually null) page in the hash.
    Accepting that exact historical hash preserves authorized chat history; all
    page/highlight data is still discarded and recomputed from SourcePage.
    """
    source = {
        key: value for key, value in citation.items()
        if key not in {"citation_id", "tool_name", "tool_call_id", "validation_status"}
    }
    identity = json.dumps(source, sort_keys=True, default=str)
    return f"CIT_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16].upper()}"


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
        parsed = (
            tool_message
            if tool_message.get("schema_version")
            else _parse_tool_content(tool_message.get("content"))
        )
        envelope = parse_evidence_envelope(parsed)
        if envelope:
            for item in envelope.get("evidence", []):
                if not isinstance(item, Mapping) or not item.get("contract_id"):
                    continue
                supplied_id = item.get("evidence_id")
                if not isinstance(supplied_id, str) or supplied_id != evidence_id(item, tenant_id):
                    continue
                locator = item.get("locator") if isinstance(item.get("locator"), Mapping) else {}
                evidence_source_type = str(item.get("source_type") or "document_metadata")
                candidates.append({
                    "citation_id": supplied_id,
                    "evidence_id": supplied_id,
                    "evidence_source_type": evidence_source_type,
                    "evidence_facts": item.get("facts") or {},
                    "evidence_locator": dict(locator),
                    "contract_id": str(item["contract_id"]),
                    "filename": item.get("filename"),
                    "source_type": (
                        "document"
                        if evidence_source_type in {"document_metadata", "document_text"}
                        else evidence_source_type
                    ),
                    "page": None,
                    "section_id": locator.get("section_id"),
                    "section_title": locator.get("section_title") or locator.get("section_type"),
                    "clause_id": locator.get("clause_id"),
                    "clause_type": locator.get("clause_type"),
                    "chunk_id": locator.get("chunk_id"),
                    "chunk_index": locator.get("chunk_index") or locator.get("chunk_order"),
                    "start_offset": locator.get("start_offset") or locator.get("start_position"),
                    "end_offset": locator.get("end_offset") or locator.get("end_position"),
                    "excerpt": item.get("excerpt"),
                    "tool_name": item.get("tool_name") or envelope.get("tool_name"),
                    "tool_call_id": item.get("tool_call_id") or envelope.get("tool_call_id"),
                })
            continue
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
                    # Tool-returned page hints are not trusted. Page and exact
                    # highlight are resolved from tenant-owned SourcePage data.
                    "page": None,
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
        # The same retrieved source can appear in multiple tool calls during
        # one answer.  Tool-call identity remains useful metadata on the
        # retained citation, but it is not source identity and must not
        # produce duplicate evidence cards.
        citation_id = candidate.pop("citation_id", None) or _citation_id(candidate)
        if citation_id in seen:
            continue
        seen.add(citation_id)
        citations.append({"citation_id": citation_id, **candidate})
    return enrich_citations_with_provenance(
        citations,
        tenant_id,
        service=PdfProvenanceService(graph_client=graph_client),
    )


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
    seen: set[str] = set()
    for citation in citations:
        if not isinstance(citation, Mapping):
            continue
        contract_id = str(citation.get("contract_id") or "")
        if contract_id not in active:
            continue
        safe = dict(citation)
        safe["filename"] = active[contract_id]
        safe["validation_status"] = "tenant_active"
        canonical_evidence_id = safe.get("evidence_id")
        if canonical_evidence_id:
            source_item = {
                "source_type": safe.get("evidence_source_type") or safe.get("source_type"),
                "contract_id": contract_id,
                "filename": safe.get("filename"),
                "facts": safe.get("evidence_facts") or {},
                "excerpt": safe.get("excerpt"),
                "locator": safe.get("evidence_locator") or {},
            }
            valid_identity = (
                safe.get("citation_id") == canonical_evidence_id
                and canonical_evidence_id == evidence_id(source_item, tenant_id)
            )
        else:
            valid_identity = safe.get("citation_id") in {
                _citation_id(safe),
                _legacy_citation_id(safe),
            }
        if not valid_identity:
            # Any manipulated contract/source/excerpt identifier invalidates
            # the stored citation. Derived page/highlight fields are excluded
            # above because they are discarded and recomputed below.
            continue
        identity = _citation_source_identity(safe)
        if identity in seen:
            continue
        seen.add(identity)
        validated.append(safe)
    return enrich_citations_with_provenance(
        validated,
        tenant_id,
        service=PdfProvenanceService(graph_client=graph_client),
    )
