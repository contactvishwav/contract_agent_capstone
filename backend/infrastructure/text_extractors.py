import asyncio
import io
import os
import logging
from dataclasses import dataclass
from typing import List

from starlette.concurrency import run_in_threadpool

from backend.domain.entities import ITextExtractor
from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Real, enforced timeout around PDF text extraction - found live during a
# real production incident: PyPDFExtractor.extract_text() on a 280KB/
# 15-page contract took ~1.4s locally (unthrottled CPU) but ~75s in
# production under real GCP e2-micro CPU-credit exhaustion - reproduced
# directly, not assumed (throttling the identical call to 0.5 CPU cores
# measured 3.2s, to 0.05 cores measured >180s without finishing). A
# pathologically large or malformed PDF under the same resource pressure
# could hang far longer with nothing bounding it today. Mirrors the same
# honest-timeout discipline already applied to every other external/
# unpredictable-duration call in this codebase (reranker_service.py's
# RERANKER_TIMEOUT_SECONDS, llm_extraction_service.py's get_default_llm
# request_timeout) - fails fast and visibly instead of hanging
# indefinitely. Not a circuit breaker: unlike a flaky external API call,
# a slow/malformed local PDF is not a "dependency that might recover if
# we back off" - a timeout is the right-shaped safety net here, a
# stateful breaker is not.
EXTRACTION_TIMEOUT_SECONDS = float(os.getenv("EXTRACTION_TIMEOUT_SECONDS", "45.0"))


class ExtractionTimeoutError(Exception):
    """Raised when text extraction exceeds EXTRACTION_TIMEOUT_SECONDS."""


@dataclass(frozen=True)
class ExtractedPage:
    """One PDF page with offsets into the legacy concatenated text."""

    page_number: int
    text: str
    global_start: int
    global_end: int
    has_text_layer: bool


@dataclass(frozen=True)
class PageAwareExtraction:
    """Page-preserving extraction without changing the historical text shape."""

    full_text: str
    pages: List[ExtractedPage]
    extraction_method: str


def _page_aware_result(page_texts: List[str], extraction_method: str) -> PageAwareExtraction:
    # Preserve the exact historical join contract: every non-empty page had a
    # trailing newline and the final aggregate was stripped.  Offsets therefore
    # remain compatible with existing whole-document chunking strategies.
    pieces: List[str] = []
    pages: List[ExtractedPage] = []
    cursor = 0
    for page_number, raw_text in enumerate(page_texts, start=1):
        text = raw_text or ""
        piece = f"{text}\n" if text else ""
        pages.append(
            ExtractedPage(
                page_number=page_number,
                text=text,
                global_start=cursor,
                global_end=cursor + len(text),
                has_text_layer=bool(text.strip()),
            )
        )
        pieces.append(piece)
        cursor += len(piece)
    return PageAwareExtraction(
        full_text="".join(pieces).strip(),
        pages=pages,
        extraction_method=extraction_method,
    )

class PyPDFExtractor(ITextExtractor):
    """PDF text extraction using pypdf - Strategy Pattern implementation"""
    
    def extract_text(self, file_path: str) -> str:
        return self.extract_pages(file_path).full_text

    def extract_pages(self, file_path: str) -> PageAwareExtraction:
        try:
            import pypdf
            with open(file_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                return _page_aware_result(
                    [page.extract_text() or "" for page in reader.pages],
                    "pypdf-text-layer-v1",
                )
        except Exception as e:
            logger.error(f"PyPDF extraction failed: {e}")
            raise

    def extract_pages_from_bytes(self, content: bytes) -> PageAwareExtraction:
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            return _page_aware_result(
                [page.extract_text() or "" for page in reader.pages],
                "pypdf-text-layer-v1",
            )
        except Exception as e:
            logger.error(f"PyPDF byte extraction failed: {e}")
            raise

class PDFPlumberExtractor(ITextExtractor):
    """Alternative PDF extraction using pdfplumber - Fallback strategy"""
    
    def extract_text(self, file_path: str) -> str:
        return self.extract_pages(file_path).full_text

    def extract_pages(self, file_path: str) -> PageAwareExtraction:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                return _page_aware_result(
                    [page.extract_text() or "" for page in pdf.pages],
                    "pdfplumber-text-layer-v1",
                )
        except Exception as e:
            logger.error(f"PDFPlumber extraction failed: {e}")
            raise

class TextExtractionService:
    """Chain of Responsibility pattern for text extraction with fallback"""
    
    def __init__(self):
        self.extractors = [
            PyPDFExtractor(),
            PDFPlumberExtractor()
        ]
    
    def extract_with_fallback(self, file_path: str) -> str:
        """Try multiple extraction methods until one succeeds"""
        last_error = None
        
        for extractor in self.extractors:
            try:
                text = extractor.extract_text(file_path)
                if text and len(text.strip()) > 50:  # Quality check
                    logger.info(f"Successfully extracted text using {extractor.__class__.__name__}")
                    return text
            except Exception as e:
                last_error = e
                logger.warning(f"{extractor.__class__.__name__} failed: {e}")
                continue

        raise Exception(f"All text extraction methods failed. Last error: {last_error}")

    def extract_pages_with_fallback(self, file_path: str) -> PageAwareExtraction:
        last_error = None
        for extractor in self.extractors:
            try:
                result = extractor.extract_pages(file_path)
                if result.full_text and len(result.full_text.strip()) > 50:
                    logger.info(
                        "Successfully extracted page-aware text using %s",
                        extractor.__class__.__name__,
                    )
                    return result
            except Exception as e:
                last_error = e
                logger.warning("%s page extraction failed: %s", extractor.__class__.__name__, e)
        raise Exception(f"All page-aware text extraction methods failed. Last error: {last_error}")

    def extract_pages_from_bytes(self, content: bytes) -> PageAwareExtraction:
        # Byte extraction is used only for exact-hash duplicate backfill.  Keep
        # the same primary extractor as normal uploads so page/global offsets
        # remain reproducible.  pdfplumber's BytesIO fallback is intentionally
        # omitted until it is needed and covered; failure remains explicit.
        return PyPDFExtractor().extract_pages_from_bytes(content)


async def extract_text_async(service: "TextExtractionService", file_path: str) -> str:
    """
    Async entry point for TextExtractionService.extract_with_fallback -
    the synchronous, CPU-bound method above. Runs it in a worker thread
    (starlette.concurrency.run_in_threadpool, the same helper FastAPI's
    own sync-route support uses internally) instead of the event loop,
    bounded by EXTRACTION_TIMEOUT_SECONDS.

    Found live during a real production incident: calling
    extract_with_fallback(...) directly inside an async route handler
    (no thread offload) blocked the entire backend process - including
    its own health check and unrelated requests from other users - for
    the full duration of one slow extraction, because this process runs
    as a single Uvicorn worker (`fastapi run backend/main.py`, no
    `--workers` flag) with one event loop. This is the fix for that:
    every async call site that needs extracted text should call this,
    not TextExtractionService.extract_with_fallback directly.
    """
    try:
        return await asyncio.wait_for(
            run_in_threadpool(service.extract_with_fallback, file_path),
            timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"PDF text extraction exceeded {EXTRACTION_TIMEOUT_SECONDS}s for {file_path}"
        )
        raise ExtractionTimeoutError(
            f"PDF text extraction exceeded {EXTRACTION_TIMEOUT_SECONDS}s for {file_path}"
        )


async def extract_pages_async(
    service: "TextExtractionService", file_path: str
) -> PageAwareExtraction:
    """Bounded, thread-offloaded page-aware counterpart to extract_text_async."""
    try:
        return await asyncio.wait_for(
            run_in_threadpool(service.extract_pages_with_fallback, file_path),
            timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Page-aware PDF extraction exceeded %ss for %s",
            EXTRACTION_TIMEOUT_SECONDS,
            file_path,
        )
        raise ExtractionTimeoutError(
            f"PDF text extraction exceeded {EXTRACTION_TIMEOUT_SECONDS}s for {file_path}"
        )


async def extract_pages_from_bytes_async(
    service: "TextExtractionService", content: bytes
) -> PageAwareExtraction:
    """Bounded page extraction for an exact-hash duplicate upload backfill."""
    try:
        return await asyncio.wait_for(
            run_in_threadpool(service.extract_pages_from_bytes, content),
            timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error("Page-aware PDF byte extraction exceeded %ss", EXTRACTION_TIMEOUT_SECONDS)
        raise ExtractionTimeoutError(
            f"PDF text extraction exceeded {EXTRACTION_TIMEOUT_SECONDS}s"
        )
