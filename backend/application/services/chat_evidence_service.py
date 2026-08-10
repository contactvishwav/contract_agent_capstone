"""Canonical, provider-neutral evidence for Contract Chat.

Tool return values are application data, not instructions.  This module turns
the heterogeneous search/MCP shapes into a bounded envelope that is used by the
answer model, citation builder, and Output Guard.  Tenant authorization is
rechecked before contract-owned records enter the envelope.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

EVIDENCE_SCHEMA_VERSION = "chat-evidence-v1"
MAX_EVIDENCE_ITEMS = 80
MAX_EXCERPT_LENGTH = 1200
MAX_FACT_STRING_LENGTH = 500

SOURCE_TYPES = {
    "document_metadata",
    "document_text",
    "section",
    "clause",
    "chunk",
    "relationship",
    "deterministic_aggregation",
    "policy_rule",
    "analysis_result",
    "image_attachment",
}

_COLLECTION_TYPES = {
    "contracts": "document_metadata",
    "documents": "document_metadata",
    "sections": "section",
    "clauses": "clause",
    "chunks": "chunk",
    "relationships": "relationship",
    "rules": "policy_rule",
    "precedent_matches": "analysis_result",
    "analysis_results": "analysis_result",
}
_TEXT_KEYS = ("content", "context", "summary", "text", "snippet", "rule_text")
_LOCATOR_KEYS = (
    "page_number",
    "section_id",
    "section_title",
    "section_type",
    "clause_id",
    "clause_type",
    "chunk_id",
    "chunk_index",
    "chunk_order",
    "start_offset",
    "end_offset",
    "start_position",
    "end_position",
)
_SCORE_KEYS = ("similarity_score", "score", "quality_score", "confidence")


def _parse(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(value)
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
    return value


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return " ".join(value.split())[:MAX_FACT_STRING_LENGTH]
    if isinstance(value, Mapping):
        return {
            str(key): bounded
            for key, child in list(value.items())[:30]
            if (bounded := _bounded(child, depth=depth + 1)) is not None
        }
    if isinstance(value, (list, tuple)):
        return [
            bounded
            for child in list(value)[:20]
            if (bounded := _bounded(child, depth=depth + 1)) is not None
        ]
    return str(value)[:MAX_FACT_STRING_LENGTH]


def _excerpt(record: Mapping[str, Any]) -> Optional[str]:
    for key in _TEXT_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:MAX_EXCERPT_LENGTH]
    return None


def _contract_id(record: Mapping[str, Any]) -> Optional[str]:
    value = record.get("contract_id") or record.get("file_id")
    return str(value) if value else None


def _filename(record: Mapping[str, Any]) -> Optional[str]:
    value = record.get("filename")
    return str(value) if value else None


def _facts(record: Mapping[str, Any]) -> dict[str, Any]:
    excluded = set(_TEXT_KEYS) | set(_LOCATOR_KEYS) | set(_SCORE_KEYS) | {
        "tenant_id",
        "embedding",
    }
    return {
        str(key): bounded
        for key, value in record.items()
        if key not in excluded and (bounded := _bounded(value)) is not None
    }


def _locator(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: bounded
        for key in _LOCATOR_KEYS
        if (bounded := _bounded(record.get(key))) is not None
    }


def _retrieval_score(record: Mapping[str, Any]) -> Optional[float]:
    for key in _SCORE_KEYS:
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def evidence_id(item: Mapping[str, Any], tenant_id: str) -> str:
    """Stable identity for source/fact content, independent of provider/call."""
    identity = {
        "tenant_id": tenant_id,
        "source_type": item.get("source_type"),
        "contract_id": item.get("contract_id"),
        "locator": item.get("locator") or {},
        "excerpt": item.get("excerpt"),
        "facts": item.get("facts") or {},
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return f"EVID_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20].upper()}"


def _active_contracts(tenant_id: str, contract_ids: Iterable[str], graph_client: Any) -> dict[str, str]:
    ids = sorted({str(value) for value in contract_ids if value})
    if not ids:
        return {}
    rows = graph_client.query(
        """
        MATCH (c:Contract)
        WHERE c.tenant_id = $tenant_id
          AND c.file_id IN $contract_ids
          AND coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
        RETURN c.file_id AS contract_id, c.filename AS filename
        """,
        {"tenant_id": tenant_id, "contract_ids": ids},
    )
    return {
        str(row["contract_id"]): str(row.get("filename") or row["contract_id"])
        for row in rows
    }


def _record_candidates(value: Any) -> list[tuple[str, Mapping[str, Any]]]:
    candidates: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        for collection, source_type in _COLLECTION_TYPES.items():
            records = value.get(collection)
            if isinstance(records, list):
                for record in records:
                    if not isinstance(record, Mapping):
                        continue
                    # ``all``-level enhanced search groups the individual
                    # Neo4j result rows under keys such as ``documents`` and
                    # ``chunks``.  Those rows are wrappers of the form
                    # {"result": {"contracts": [...]}} rather than source
                    # records themselves.  Unwrap them recursively so the
                    # tenant-owned IDs, locators, and excerpts survive.
                    if isinstance(record.get("result"), (Mapping, list, tuple)):
                        candidates.extend(_record_candidates(record["result"]))
                    else:
                        candidates.append((source_type, record))
        metadata = value.get("metadata")
        if isinstance(metadata, Mapping):
            candidates.append(("document_metadata", metadata))
        for key, child in value.items():
            if key not in _COLLECTION_TYPES and key != "metadata":
                candidates.extend(_record_candidates(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            candidates.extend(_record_candidates(child))
    return candidates


def _aggregation_candidates(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        total_count = value.get("total_count")
        if isinstance(total_count, int) and total_count >= 0:
            collection = next(
                (key for key in _COLLECTION_TYPES if isinstance(value.get(key), list)),
                "records",
            )
            records = value.get(collection) if collection != "records" else []
            records = records if isinstance(records, list) else []
            candidates.append({
                "operation": "count",
                "collection": collection,
                "total_count": total_count,
                "returned_count": len(records),
                "contract_ids": sorted({
                    contract_id
                    for record in records
                    if isinstance(record, Mapping)
                    if (contract_id := _contract_id(record))
                }),
                "filenames": sorted({
                    filename
                    for record in records
                    if isinstance(record, Mapping)
                    if (filename := _filename(record))
                }),
            })
        for child in value.values():
            candidates.extend(_aggregation_candidates(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            candidates.extend(_aggregation_candidates(child))
    return candidates


def build_evidence_envelope(
    result: Any,
    *,
    tenant_id: str,
    tool_name: str,
    tool_call_id: Optional[str],
    graph_client: Any = None,
) -> dict[str, Any]:
    """Normalize one trusted, server-scoped tool result into evidence."""
    if graph_client is None:
        # Keep governance/validator imports side-effect free. Importing the
        # global graph at module load initialized a real Neo4j connection
        # before isolated route tests could install their test double.
        from backend.shared.utils.contract_search_tool import graph

        graph_client = graph
    parsed = _parse(result)
    tool_failed = isinstance(parsed, Mapping) and parsed.get("success") is False
    records = _record_candidates(parsed)
    active = _active_contracts(
        tenant_id,
        (_contract_id(record) for _, record in records),
        graph_client,
    )

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_type, record in records:
        contract_id = _contract_id(record)
        if contract_id and contract_id not in active:
            continue
        item = {
            "source_type": source_type,
            "contract_id": contract_id,
            "filename": active.get(contract_id) if contract_id else _filename(record),
            "facts": _facts(record),
            "excerpt": _excerpt(record),
            "locator": _locator(record),
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "retrieval_score": _retrieval_score(record),
            "verification_status": "tenant_active" if contract_id else "tenant_authoritative",
        }
        item["evidence_id"] = evidence_id(item, tenant_id)
        if item["evidence_id"] in seen:
            continue
        seen.add(item["evidence_id"])
        items.append(item)

    for facts in _aggregation_candidates(parsed):
        # Count/list facts are authoritative because this function is called
        # immediately after a server-injected, tenant-scoped tool execution.
        # Drop any record identifiers that failed the active-tenant recheck.
        original_contract_ids = list(facts["contract_ids"])
        if original_contract_ids and any(cid not in active for cid in original_contract_ids):
            continue
        facts["contract_ids"] = [cid for cid in original_contract_ids if cid in active]
        facts["filenames"] = sorted({active[cid] for cid in facts["contract_ids"]})
        item = {
            "source_type": "deterministic_aggregation",
            "contract_id": None,
            "filename": None,
            "facts": facts,
            "excerpt": None,
            "locator": {},
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "retrieval_score": None,
            "verification_status": "tenant_authoritative",
        }
        item["evidence_id"] = evidence_id(item, tenant_id)
        if item["evidence_id"] not in seen:
            seen.add(item["evidence_id"])
            items.append(item)

    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_status": "failure" if tool_failed else "success",
        "tool_error_category": "tool_execution_failed" if tool_failed else None,
        "evidence": items[:MAX_EVIDENCE_ITEMS],
    }


def image_attachment_evidence_item(attachment_id: str, tenant_id: str, mime_type: str) -> dict[str, Any]:
    """One evidence item representing an image the responding model
    directly examined in this turn (ADR-004 addendum, ADR-008) - not
    retrieved contract text, but a real, verifiable input the same model
    producing the answer actually saw. Lets HallucinationValidator's LLM
    auditor treat image-describing claims as grounded (see hallucination.py's
    updated guideline) without weakening its existing, separate requirement
    that legal/contract claims still need real chunk/section/clause/
    document_text evidence: "image_attachment" is deliberately NOT one of
    the source types in hallucination.py's `text_source_present` check, so
    a turn that mixes an image with legal-term contract claims still
    requires real contract evidence for those claims, unchanged.

    contract_id is always None (an attachment isn't contract-owned content),
    so this item is automatically skipped by chat_citation_service.py's
    citation builder (it requires a contract_id) - no bogus "citation" is
    ever created for an attached image, same as how deterministic_aggregation
    evidence (also contract_id=None) already safely skips citation building.
    """
    item: dict[str, Any] = {
        "source_type": "image_attachment",
        "contract_id": None,
        "filename": None,
        "facts": {"attachment_id": attachment_id, "mime_type": mime_type},
        "excerpt": None,
        "locator": {},
        "tool_name": None,
        "tool_call_id": None,
        "retrieval_score": None,
        "verification_status": "tenant_authoritative",
    }
    item["evidence_id"] = evidence_id(item, tenant_id)
    return item


def parse_evidence_envelope(value: Any) -> Optional[dict[str, Any]]:
    parsed = _parse(value)
    if not isinstance(parsed, Mapping):
        return None
    if parsed.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        return None
    evidence = parsed.get("evidence")
    if not isinstance(evidence, list):
        return None
    return dict(parsed)


def combine_evidence_envelopes(
    envelopes: Iterable[Mapping[str, Any]], tenant_id: str
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    tool_calls: list[dict[str, Any]] = []
    for envelope in envelopes:
        tool_calls.append({
            "tool_name": envelope.get("tool_name"),
            "tool_call_id": envelope.get("tool_call_id"),
            "tool_status": envelope.get("tool_status"),
            "tool_error_category": envelope.get("tool_error_category"),
        })
        for item in envelope.get("evidence", []):
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("evidence_id")
            if not item_id or item_id in seen:
                continue
            seen.add(str(item_id))
            items.append(dict(item))
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "tool_calls": tool_calls,
        "evidence": items[:MAX_EVIDENCE_ITEMS],
    }


def evidence_summary(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded observability only; never returns excerpts, facts, or tenant IDs."""
    counts: dict[str, int] = {}
    contracts: set[str] = set()
    citations = 0
    for item in envelope.get("evidence", []):
        if not isinstance(item, Mapping):
            continue
        source_type = str(item.get("source_type") or "unknown")
        counts[source_type] = counts.get(source_type, 0) + 1
        if item.get("contract_id"):
            contracts.add(str(item["contract_id"]))
        if item.get("locator"):
            citations += 1
    return {
        "evidence_count": sum(counts.values()),
        "source_type_counts": counts,
        "contract_count": len(contracts),
        "located_evidence_count": citations,
    }


def render_deterministic_metadata_answer(
    prompt: str,
    envelope: Mapping[str, Any],
) -> Optional[str]:
    """Render bounded catalog/count/type/party answers from authoritative facts.

    This is not an LLM rewrite.  It prevents a provider from expanding a simple
    catalog request into legal claims that document metadata cannot ground.
    """
    normalized = " ".join(prompt.lower().split())
    if re.search(
        r"\b(compare|comparison|all contracts?|every contract|across contracts?)\b",
        normalized,
    ):
        return None
    metadata_items = [
        item
        for item in envelope.get("evidence", [])
        if isinstance(item, Mapping) and item.get("source_type") == "document_metadata"
    ]
    aggregations = [
        item
        for item in envelope.get("evidence", [])
        if isinstance(item, Mapping)
        and item.get("source_type") == "deterministic_aggregation"
        and (item.get("facts") or {}).get("collection") in {"contracts", "documents"}
    ]
    total = next(
        (
            item["facts"]["total_count"]
            for item in aggregations
            if isinstance((item.get("facts") or {}).get("total_count"), int)
        ),
        None,
    )

    if re.search(r"\b(how many|count)\b", normalized):
        if total is None:
            return None
        noun = "contract" if total == 1 else "contracts"
        return f"There {'is' if total == 1 else 'are'} {total} active {noun} available."

    if re.search(r"\b(contract types?|types of contracts?)\b", normalized):
        types = sorted({
            str((item.get("facts") or {}).get("contract_type"))
            for item in metadata_items
            if (item.get("facts") or {}).get("contract_type")
        })
        if not types:
            return "No contract types were found in the active contract metadata."
        return "Active contract types:\n" + "\n".join(f"- {value}" for value in types)

    if re.search(r"\b(parties|party)\b", normalized):
        lines = []
        for item in metadata_items:
            filename = item.get("filename") or "Contract"
            parties = (item.get("facts") or {}).get("parties")
            if not isinstance(parties, list):
                continue
            labels = []
            for party in parties:
                if isinstance(party, Mapping) and party.get("name"):
                    role = f" ({party['role']})" if party.get("role") else ""
                    labels.append(f"{party['name']}{role}")
            if labels:
                lines.append(f"- {filename}: {', '.join(labels)}")
        if not lines:
            return "No party metadata was found for the active contracts."
        return "Parties in the active contract metadata:\n" + "\n".join(lines)

    if re.search(r"\b(list|available|which contracts?|what contracts?)\b", normalized):
        if total == 0:
            return "No active contracts are available."
        if not metadata_items:
            return None
        lines = []
        for item in sorted(metadata_items, key=lambda value: str(value.get("filename") or "")):
            filename = item.get("filename") or "Unnamed contract"
            contract_type = (item.get("facts") or {}).get("contract_type")
            lines.append(f"- {filename}" + (f" — {contract_type}" if contract_type else ""))
        heading = (
            f"The following {total} active contracts are available:"
            if total is not None
            else "The following active contracts are available:"
        )
        return heading + "\n" + "\n".join(lines)
    return None
