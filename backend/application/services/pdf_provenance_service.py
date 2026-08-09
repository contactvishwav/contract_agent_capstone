"""Durable source-PDF/page provenance and deterministic citation resolution."""

from __future__ import annotations

import unicodedata
import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from backend.infrastructure.encryption import DecryptionError, field_encryptor
from backend.infrastructure.pdf_source_storage import (
    PDF_SOURCE_STORAGE_VERSION,
    PdfSourceStorage,
    pdf_source_storage,
)
from backend.infrastructure.text_extractors import ExtractedPage, PageAwareExtraction
from backend.shared.utils.contract_search_tool import graph


@dataclass(frozen=True)
class ExactPageMatch:
    page_number: int
    start_offset: int
    end_offset: int


def _normalized_with_map(value: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    source_offsets: list[int] = []
    pending_space: Optional[int] = None
    for source_offset, char in enumerate(unicodedata.normalize("NFKC", value or "")):
        if char.isspace():
            if normalized_chars and pending_space is None:
                pending_space = source_offset
            continue
        if pending_space is not None:
            normalized_chars.append(" ")
            source_offsets.append(pending_space)
            pending_space = None
        folded = char.casefold()
        normalized_chars.extend(folded)
        source_offsets.extend([source_offset] * len(folded))
    return "".join(normalized_chars), source_offsets


def locate_unique_page_match(text: str, pages: Iterable[ExtractedPage]) -> Optional[ExactPageMatch]:
    needle, _ = _normalized_with_map(text)
    if len(needle) < 12:
        return None
    matches: list[ExactPageMatch] = []
    for page in pages:
        haystack, offsets = _normalized_with_map(page.text)
        start = 0
        while True:
            found = haystack.find(needle, start)
            if found < 0:
                break
            raw_start = offsets[found]
            raw_end = offsets[found + len(needle) - 1] + 1
            matches.append(ExactPageMatch(page.page_number, raw_start, raw_end))
            if len(matches) > 1:
                return None
            start = found + 1
    return matches[0] if len(matches) == 1 else None


def _page_for_global_span(
    start_position: Any,
    end_position: Any,
    pages: Iterable[ExtractedPage],
) -> Optional[ExtractedPage]:
    if not isinstance(start_position, int) or not isinstance(end_position, int):
        return None
    if start_position < 0 or end_position <= start_position:
        return None
    for page in pages:
        if start_position >= page.global_start and end_position <= page.global_end:
            return page
    return None


def _safe_decrypt(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return field_encryptor.decrypt(value)
    except DecryptionError:
        return ""


class PdfProvenanceService:
    def __init__(self, graph_client: Any = graph, storage: PdfSourceStorage = pdf_source_storage):
        self.graph = graph_client
        self.storage = storage

    def persist_source(
        self,
        *,
        contract_id: str,
        tenant_id: str,
        filename: str,
        pdf_bytes: bytes,
        extraction: PageAwareExtraction,
        source_hash: str,
    ) -> None:
        """Idempotently retain one exact-hash PDF and its page text."""
        rows = self.graph.query(
            "MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id}) "
            "WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE' "
            "AND c.source_hash = $source_hash RETURN c.file_id AS contract_id",
            {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                "source_hash": source_hash,
            },
        )
        if not rows:
            raise ValueError("Active contract/source hash could not be verified")

        storage_key = self.storage.store(tenant_id, contract_id, pdf_bytes)
        has_text_layer = any(page.has_text_layer for page in extraction.pages)
        updated = self.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
              AND c.source_hash = $source_hash
            SET c.source_storage_key = $storage_key,
                c.source_storage_version = $storage_version,
                c.source_pdf_available = true,
                c.source_page_count = $page_count,
                c.source_has_text_layer = $has_text_layer,
                c.source_extraction_method = $extraction_method,
                c.source_filename = $filename,
                c.source_provenance_updated_at = datetime()
            RETURN c.file_id AS contract_id
            """,
            {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                "source_hash": source_hash,
                "storage_key": storage_key,
                "storage_version": PDF_SOURCE_STORAGE_VERSION,
                "page_count": len(extraction.pages),
                "has_text_layer": has_text_layer,
                "extraction_method": extraction.extraction_method,
                "filename": filename,
            },
        )
        if not updated:
            raise RuntimeError("Source PDF metadata was not persisted")

        for page in extraction.pages:
            page_id = hashlib.sha256(
                f"page-provenance-v1:{tenant_id}:{contract_id}:{page.page_number}".encode("utf-8")
            ).hexdigest()
            self.graph.query(
                """
                MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
                WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
                MERGE (p:SourcePage {page_id: $page_id})
                SET p.contract_id = $contract_id,
                    p.tenant_id = $tenant_id,
                    p.page_number = $page_number,
                    p.text = $text,
                    p.global_start = $global_start,
                    p.global_end = $global_end,
                    p.has_text_layer = $has_text_layer,
                    p.extraction_method = $extraction_method,
                    p.updated_at = datetime()
                MERGE (c)-[:HAS_SOURCE_PAGE]->(p)
                """,
                {
                    "contract_id": contract_id,
                    "tenant_id": tenant_id,
                    "page_id": page_id,
                    "page_number": page.page_number,
                    "text": field_encryptor.encrypt(page.text),
                    "global_start": page.global_start,
                    "global_end": page.global_end,
                    "has_text_layer": page.has_text_layer,
                    "extraction_method": extraction.extraction_method,
                },
            )

        self._map_chunks(contract_id, tenant_id, extraction.pages)

    def _map_chunks(
        self,
        contract_id: str,
        tenant_id: str,
        pages: list[ExtractedPage],
    ) -> None:
        rows = self.graph.query(
            """
            MATCH (d:Document {contract_id: $contract_id, tenant_id: $tenant_id})-[:HAS_CHUNK]->(n:Chunk)
            RETURN n.id AS node_id, n.content AS content,
                   n.start_position AS start_position, n.end_position AS end_position
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )
        for row in rows:
            content = _safe_decrypt(row.get("content"))
            exact = locate_unique_page_match(content, pages) if content else None
            page = next((p for p in pages if exact and p.page_number == exact.page_number), None)
            status = "exact_chunk" if exact else "page_only"
            if page is None:
                page = _page_for_global_span(
                    row.get("start_position"), row.get("end_position"), pages
                )
            if page is None:
                continue
            page_start = exact.start_offset if exact else row.get("start_position") - page.global_start
            page_end = exact.end_offset if exact else row.get("end_position") - page.global_start
            self.graph.query(
                """
                MATCH (d:Document {contract_id: $contract_id, tenant_id: $tenant_id})-[:HAS_CHUNK]->(n:Chunk {id: $node_id})
                SET n.page_number = $page_number,
                    n.page_start_offset = $page_start,
                    n.page_end_offset = $page_end,
                    n.provenance_status = $status,
                    n.provenance_version = $version
                """,
                {
                    "contract_id": contract_id,
                    "tenant_id": tenant_id,
                    "node_id": row["node_id"],
                    "page_number": page.page_number,
                    "page_start": page_start,
                    "page_end": page_end,
                    "status": status,
                    "version": extraction_version(pages),
                },
            )

    def source_record(self, contract_id: str, tenant_id: str) -> Optional[dict[str, Any]]:
        rows = self.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})
            WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
              AND c.source_pdf_available = true
            RETURN c.source_storage_key AS storage_key,
                   coalesce(c.source_filename, c.filename) AS filename,
                   c.source_page_count AS page_count,
                   c.source_has_text_layer AS has_text_layer,
                   c.source_extraction_method AS extraction_method
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )
        return rows[0] if rows else None

    def load_pages(self, contract_id: str, tenant_id: str) -> list[ExtractedPage]:
        rows = self.graph.query(
            """
            MATCH (c:Contract {file_id: $contract_id, tenant_id: $tenant_id})-[:HAS_SOURCE_PAGE]->(p:SourcePage {tenant_id: $tenant_id})
            WHERE coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
            RETURN p.page_number AS page_number, p.text AS text,
                   p.global_start AS global_start, p.global_end AS global_end,
                   p.has_text_layer AS has_text_layer
            ORDER BY p.page_number
            """,
            {"contract_id": contract_id, "tenant_id": tenant_id},
        )
        return [
            ExtractedPage(
                page_number=int(row["page_number"]),
                text=_safe_decrypt(row.get("text")),
                global_start=int(row.get("global_start") or 0),
                global_end=int(row.get("global_end") or 0),
                has_text_layer=bool(row.get("has_text_layer")),
            )
            for row in rows
        ]

    def mapped_chunk_pages(
        self,
        contract_id: str,
        tenant_id: str,
        chunk_ids: list[str],
    ) -> dict[str, int]:
        if not chunk_ids:
            return {}
        rows = self.graph.query(
            """
            MATCH (d:Document {contract_id: $contract_id, tenant_id: $tenant_id})-[:HAS_CHUNK]->(n:Chunk)
            WHERE n.id IN $chunk_ids AND n.page_number IS NOT NULL
            RETURN n.id AS chunk_id, n.page_number AS page_number
            """,
            {
                "contract_id": contract_id,
                "tenant_id": tenant_id,
                "chunk_ids": chunk_ids,
            },
        )
        return {str(row["chunk_id"]): int(row["page_number"]) for row in rows}


def extraction_version(_pages: list[ExtractedPage]) -> str:
    return "page-provenance-v1"


def enrich_citations_with_provenance(
    citations: list[dict[str, Any]],
    tenant_id: str,
    *,
    service: Optional[PdfProvenanceService] = None,
) -> list[dict[str, Any]]:
    """Replace untrusted locator hints with verified source/page evidence."""
    provenance = service or PdfProvenanceService()
    by_contract: dict[str, list[dict[str, Any]]] = {}
    for citation in citations:
        by_contract.setdefault(str(citation["contract_id"]), []).append(citation)

    for contract_id, contract_citations in by_contract.items():
        source = provenance.source_record(contract_id, tenant_id)
        pages = provenance.load_pages(contract_id, tenant_id) if source else []
        mapped_pages = provenance.mapped_chunk_pages(
            contract_id,
            tenant_id,
            [str(c["chunk_id"]) for c in contract_citations if c.get("chunk_id")],
        )
        for citation in contract_citations:
            citation["page"] = None
            citation["highlight_text"] = None
            citation["page_start_offset"] = None
            citation["page_end_offset"] = None
            citation["source_available"] = bool(source)
            citation["provenance_status"] = "legacy_excerpt"
            if not source:
                continue
            if not pages or not any(page.has_text_layer for page in pages):
                citation["provenance_status"] = "unsupported_image_only"
                continue
            excerpt = citation.get("excerpt")
            exact = locate_unique_page_match(excerpt, pages) if isinstance(excerpt, str) else None
            if exact:
                citation.update(
                    {
                        "page": exact.page_number,
                        "highlight_text": excerpt,
                        "page_start_offset": exact.start_offset,
                        "page_end_offset": exact.end_offset,
                        "provenance_status": "exact",
                    }
                )
                continue
            mapped_page = mapped_pages.get(str(citation.get("chunk_id") or ""))
            if mapped_page:
                citation["page"] = mapped_page
                citation["provenance_status"] = "page_only"
            else:
                citation["provenance_status"] = "source_excerpt_only"
    return citations
