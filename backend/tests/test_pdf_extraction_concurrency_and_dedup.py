"""
Regression tests for a real production incident: uploading a PDF via
POST /api/documents/upload returned a 504 Gateway Timeout, and the
backend was unresponsive to its own health check for the full duration
of the request. Root-caused live, not assumed:

1. PyPDFExtractor.extract_text() (backend/infrastructure/text_extractors.py)
   is called synchronously, directly inside an async route handler, with
   no thread offload - this process runs as a single Uvicorn worker
   (`fastapi run backend/main.py`, no --workers flag), so a slow,
   CPU-bound extraction call blocks the entire event loop, including
   concurrent requests to completely unrelated routes (confirmed live:
   even the Docker healthcheck's own probe 504'd during the incident).
   Reproduced directly: the exact production PDF (data/Salesforce_MSA.pdf,
   280,331 bytes, byte-identical to the file logged in production)
   extracts in 1.4s at full CPU, 3.2s throttled to 0.5 cores, and over
   180s (didn't finish) throttled to 0.05 cores - proving genuine CPU
   starvation on the production VM is fully sufficient to explain the
   incident's ~94s duration, no code inefficiency in the extractor
   itself required.

2. A second, real, independent bug found while root-causing #1:
   DocumentProcessingService._process_with_agent hardcoded
   initial_state["extracted_text"] = None, so pdf_processing_agent.py's
   extract_text_node unconditionally re-extracted the SAME file a second
   time on every single upload (confirmed in production logs: two
   distinct "successfully extracted" log lines for the same correlation
   ID), doubling that request's exposure to the same slow-extraction risk.

This file tests the fix for both: extraction runs off the event loop
with an enforced timeout (extract_text_async), and a caller-supplied
extracted_text is genuinely reused, not re-extracted.
"""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.infrastructure.text_extractors import (
    EXTRACTION_TIMEOUT_SECONDS,
    ExtractionTimeoutError,
    TextExtractionService,
    extract_text_async,
)


class ExtractionDoesNotBlockEventLoopTests(unittest.IsolatedAsyncioTestCase):
    """This is the test that would have caught the incident: the backend
    must stay responsive to a concurrent request (standing in for its own
    health check, or another user's request) while a slow extraction is
    in flight - the exact failure mode confirmed live in production."""

    async def test_concurrent_request_stays_responsive_during_slow_extraction(self):
        service = TextExtractionService()

        def slow_blocking_extract(file_path):
            time.sleep(0.6)  # genuinely blocks whatever thread runs it
            return "extracted text " * 20

        health_check_done_at = []

        async def fake_health_check():
            # A real, unrelated coroutine that only needs the event loop
            # to be free to run, exactly like a concurrent health-check
            # request would.
            await asyncio.sleep(0.05)
            health_check_done_at.append(time.perf_counter())
            return "healthy"

        with patch.object(service, "extract_with_fallback", side_effect=slow_blocking_extract):
            t0 = time.perf_counter()
            extraction_task = asyncio.create_task(extract_text_async(service, "/fake/path.pdf"))
            health_task = asyncio.create_task(fake_health_check())

            health_result = await health_task
            health_done_at = time.perf_counter()
            text = await extraction_task
            extraction_done_at = time.perf_counter()

        self.assertEqual(health_result, "healthy")
        # The health check (0.05s) must finish well before the slow
        # extraction (0.6s) - if extraction blocked the event loop (the
        # actual production bug), the health check could not run
        # concurrently at all, and this ordering would fail.
        self.assertLess(
            health_done_at - t0, 0.3,
            "health check was blocked by the slow extraction - this is the production incident recurring",
        )
        self.assertGreater(extraction_done_at - t0, 0.5)
        self.assertIn("extracted text", text)


class ExtractionTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_extraction_exceeding_timeout_raises_and_fails_fast(self):
        service = TextExtractionService()

        def much_too_slow(file_path):
            time.sleep(1.5)  # far longer than the patched timeout below
            return "should never be returned"

        with patch.object(service, "extract_with_fallback", side_effect=much_too_slow), \
             patch("backend.infrastructure.text_extractors.EXTRACTION_TIMEOUT_SECONDS", 0.2):
            t0 = time.perf_counter()
            with self.assertRaises(ExtractionTimeoutError):
                await extract_text_async(service, "/fake/path.pdf")
            elapsed = time.perf_counter() - t0

        # Must fail fast at ~the timeout, not wait for the real slow call -
        # a pathologically slow or malformed PDF must fail fast and
        # visibly instead of hanging indefinitely (matching the same
        # discipline as RERANKER_TIMEOUT_SECONDS elsewhere).
        self.assertLess(elapsed, 1.0)

    def test_default_timeout_is_a_real_positive_bound(self):
        # Basic sanity - a real, finite, positive timeout is configured
        # by default, not accidentally 0/None/disabled.
        self.assertGreater(EXTRACTION_TIMEOUT_SECONDS, 0)


class RedundantExtractionRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Regression test for the second real bug: a caller-supplied
    extracted_text must be reused by pdf_processing_agent's graph, not
    silently discarded and re-extracted from scratch."""

    async def _run_graph(self, initial_state):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.pdf_processing_agent import get_pdf_processing_agent

        # The fake LLM raises on the first real call (inside
        # analyze_contract_node, right after extraction) - this is
        # sufficient to drive the graph past extract_text_node (the node
        # under test) without needing a working Gemini client or a real
        # Neo4j write; analyze_contract_node's except branch sets
        # processing_result=ERROR, and should_continue immediately routes
        # to END once processing_result is set, so store_contract_node
        # (the only node that touches Neo4j for real) never runs.
        fake_llm = MagicMock()
        fake_llm.invoke.side_effect = RuntimeError("stop after extraction - analysis not under test here")

        with patch("backend.infrastructure.contract_repository.Neo4jContractRepository"):
            agent = get_pdf_processing_agent(fake_llm)
            return await agent.ainvoke(initial_state)

    async def test_pre_extracted_text_is_reused_not_re_extracted(self):
        call_count = {"n": 0}

        def counting_extract(self, file_path):
            call_count["n"] += 1
            return "text that should never be produced by this test"

        initial_state = {
            "file_path": "/fake/path.pdf",
            "tenant_id": "test-tenant",
            "extracted_text": "already extracted text, 500 real characters worth" * 10,
            "contract_data": None,
            "processing_result": None,
        }

        with patch("backend.infrastructure.text_extractors.TextExtractionService.extract_with_fallback", counting_extract):
            final_state = await self._run_graph(initial_state)

        self.assertEqual(call_count["n"], 0, "extraction must not run at all when extracted_text was already supplied")
        self.assertEqual(final_state["extracted_text"], initial_state["extracted_text"])

    async def test_missing_extracted_text_still_extracts_exactly_once(self):
        """Baseline/control - confirms the skip-logic above isn't just
        disabling extraction outright: when the caller genuinely hasn't
        extracted yet, extraction must still run, exactly once."""
        call_count = {"n": 0}

        def counting_extract(self, file_path):
            call_count["n"] += 1
            return "freshly extracted text"

        initial_state = {
            "file_path": "/fake/path.pdf",
            "tenant_id": "test-tenant",
            "extracted_text": None,
            "contract_data": None,
            "processing_result": None,
        }

        with patch("backend.infrastructure.text_extractors.TextExtractionService.extract_with_fallback", counting_extract):
            final_state = await self._run_graph(initial_state)

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(final_state["extracted_text"], "freshly extracted text")


if __name__ == "__main__":
    unittest.main()
