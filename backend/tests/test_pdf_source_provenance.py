import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), patch(
    "backend.shared.utils.gemini_embedding_service.embedding"
):
    from backend.api import document_upload
    from backend.application.services.pdf_provenance_service import (
        PdfProvenanceService,
        enrich_citations_with_provenance,
        locate_unique_page_match,
    )
    from backend.infrastructure.pdf_source_storage import (
        PdfSourceStorage,
        PdfSourceUnavailable,
    )
    from backend.infrastructure.text_extractors import ExtractedPage, PageAwareExtraction

from backend.tests.conftest import auth_headers


class StaticKeyProvider:
    def get_key(self):
        return b"k" * 32


def test_encrypted_pdf_storage_is_tenant_bound_and_opaque():
    with tempfile.TemporaryDirectory() as directory:
        storage = PdfSourceStorage(directory, StaticKeyProvider())
        content = b"%PDF-1.7 private legal document"
        key = storage.store("tenant_a", "CONTRACT_A", content)

        assert storage.read("tenant_a", "CONTRACT_A", key) == content
        stored = list(Path(directory).rglob("*.enc"))
        assert len(stored) == 1
        assert content not in stored[0].read_bytes()
        assert "tenant_a" not in str(stored[0])

        with pytest.raises(PdfSourceUnavailable):
            storage.read("tenant_b", "CONTRACT_A", key)
        with pytest.raises(PdfSourceUnavailable):
            storage.read("tenant_a", "CONTRACT_A", "../../etc/passwd")


def test_whitespace_normalized_highlight_requires_one_unambiguous_page_match():
    pages = [
        ExtractedPage(1, "Payment\n is due   within 90 days.", 0, 33, True),
        ExtractedPage(2, "Termination terms.", 34, 52, True),
    ]
    match = locate_unique_page_match("Payment is due within 90 days.", pages)
    assert match is not None
    assert match.page_number == 1

    ambiguous = pages + [ExtractedPage(3, "Payment is due within 90 days.", 53, 83, True)]
    assert locate_unique_page_match("Payment is due within 90 days.", ambiguous) is None


def test_citation_provenance_uses_truthful_exact_page_and_fallback_tiers():
    service = MagicMock()
    service.source_record.return_value = {"storage_key": "opaque"}
    service.load_pages.return_value = [
        ExtractedPage(1, "Payment is due within 90 days.", 0, 30, True),
        ExtractedPage(2, "Termination requires written notice.", 31, 67, True),
    ]
    service.mapped_chunk_pages.return_value = {"CHUNK_PAGE": 2}
    citations = [
        {"contract_id": "C1", "chunk_id": "CHUNK_EXACT", "excerpt": "Payment is due within 90 days."},
        {"contract_id": "C1", "chunk_id": "CHUNK_PAGE", "excerpt": "Paraphrased termination term"},
        {"contract_id": "C1", "chunk_id": "CHUNK_NONE", "excerpt": "Unmatched excerpt"},
    ]

    enriched = enrich_citations_with_provenance(citations, "tenant_a", service=service)

    assert enriched[0]["provenance_status"] == "exact"
    assert enriched[0]["page"] == 1
    assert enriched[0]["highlight_text"] == citations[0]["excerpt"]
    assert enriched[1]["provenance_status"] == "page_only"
    assert enriched[1]["page"] == 2
    assert enriched[1]["highlight_text"] is None
    assert enriched[2]["provenance_status"] == "source_excerpt_only"
    assert enriched[2]["page"] is None


def test_image_only_pdf_never_claims_a_page_or_highlight():
    service = MagicMock()
    service.source_record.return_value = {"storage_key": "opaque"}
    service.load_pages.return_value = [ExtractedPage(1, "", 0, 0, False)]
    service.mapped_chunk_pages.return_value = {"CHUNK_1": 1}
    citation = {"contract_id": "C1", "chunk_id": "CHUNK_1", "excerpt": "Scanned text"}

    enriched = enrich_citations_with_provenance([citation], "tenant_a", service=service)[0]

    assert enriched["provenance_status"] == "unsupported_image_only"
    assert enriched["page"] is None
    assert enriched["highlight_text"] is None


def test_source_page_identity_is_tenant_bound():
    page_ids = []
    graph = MagicMock()

    def query(cypher, params):
        if "RETURN c.file_id AS contract_id" in cypher:
            return [{"contract_id": params["contract_id"]}]
        if "MERGE (p:SourcePage" in cypher:
            page_ids.append((params["tenant_id"], params["page_id"]))
        return []

    graph.query.side_effect = query
    storage = MagicMock()
    storage.store.return_value = "a" * 64
    service = PdfProvenanceService(graph_client=graph, storage=storage)
    extraction = PageAwareExtraction(
        pages=[ExtractedPage(1, "Tenant-owned source text.", 0, 25, True)],
        full_text="Tenant-owned source text.",
        extraction_method="pypdf",
    )

    service.persist_source(
        contract_id="SHARED_ID", tenant_id="tenant_a", filename="a.pdf",
        pdf_bytes=b"%PDF tenant a", extraction=extraction, source_hash="hash-a",
    )
    service.persist_source(
        contract_id="SHARED_ID", tenant_id="tenant_b", filename="b.pdf",
        pdf_bytes=b"%PDF tenant b", extraction=extraction, source_hash="hash-b",
    )

    assert page_ids[0][1] != page_ids[1][1]
    assert page_ids == [("tenant_a", page_ids[0][1]), ("tenant_b", page_ids[1][1])]


app = FastAPI()
app.include_router(document_upload.router)
client = TestClient(app)


def test_authenticated_source_endpoint_uses_server_record_and_safe_headers():
    service = MagicMock()
    service.source_record.return_value = {
        "storage_key": "a" * 64,
        "filename": '../../unsafe "contract".pdf',
    }
    service.storage.read.return_value = b"%PDF-1.7 source"
    with patch(
        "backend.application.services.pdf_provenance_service.PdfProvenanceService",
        return_value=service,
    ):
        response = client.get(
            "/api/documents/CONTRACT_A/source",
            headers=auth_headers(tenant_id="tenant_a", role="ADMIN"),
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert "%2F" in response.headers["content-disposition"]
    service.source_record.assert_called_once_with("CONTRACT_A", "tenant_a")
    service.storage.read.assert_called_once_with("tenant_a", "CONTRACT_A", "a" * 64)


@pytest.mark.parametrize("source_record", [None, {}])
def test_missing_archived_and_cross_tenant_source_are_same_nondisclosing_404(source_record):
    service = MagicMock()
    service.source_record.return_value = source_record
    with patch(
        "backend.application.services.pdf_provenance_service.PdfProvenanceService",
        return_value=service,
    ):
        response = client.get(
            "/api/documents/UNKNOWN/source",
            headers=auth_headers(tenant_id="tenant_b", role="ADMIN"),
        )
    assert response.status_code == 404
    assert response.json() == {"detail": "Source PDF not found"}
    service.storage.read.assert_not_called()


def test_source_endpoint_requires_authentication():
    response = client.get("/api/documents/CONTRACT_A/source")
    assert response.status_code == 401
