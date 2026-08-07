import asyncio
import os
import logging

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

class PyPDFExtractor(ITextExtractor):
    """PDF text extraction using pypdf - Strategy Pattern implementation"""
    
    def extract_text(self, file_path: str) -> str:
        try:
            import pypdf
            with open(file_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                text = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
        except Exception as e:
            logger.error(f"PyPDF extraction failed: {e}")
            raise

class PDFPlumberExtractor(ITextExtractor):
    """Alternative PDF extraction using pdfplumber - Fallback strategy"""
    
    def extract_text(self, file_path: str) -> str:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
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