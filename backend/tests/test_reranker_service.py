"""
Regression tests for backend/agents/reranker_service.py - real, second-
stage re-ranking for semantic contract/clause search.

Circuit-breaker tests follow test_circuit_breaker_wiring.py's established
pattern exactly: patch the module's circuit breaker reference with a
fresh, low-threshold CircuitBreaker (never the shared singleton, so
tripping it here can't leak into other tests), then prove the underlying
LLM's call count stops increasing once it's open.
"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.agents.reranker_service import (
    RerankerService, RerankOutcome, _RerankResponse, _RerankedItem,
)
from backend.shared.cache.redis_cache import cache
from backend.shared.reliability.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


def _wrap(response, usage=None):
    return {
        "raw": SimpleNamespace(usage_metadata=usage or {"input_tokens": 50, "output_tokens": 20}),
        "parsed": response,
        "parsing_error": None,
    }


def _make_fake_llm(*responses):
    structured = MagicMock()
    structured.invoke.side_effect = [_wrap(r) for r in responses]
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value = structured
    fake_llm.model = "fake-reranker-model"
    return fake_llm, structured


CANDIDATES = [
    {"file_id": "a", "summary": "A generic non-disclosure agreement between two parties."},
    {"file_id": "b", "summary": "A master services agreement with a liability cap of $1,000,000."},
    {"file_id": "c", "summary": "An equipment lease with no liability provisions at all."},
]


class RerankingChangesOrderTests(unittest.TestCase):
    """Constructed case: cosine similarity (the original vector-index
    order, simulated here by candidate list order) disagrees with what a
    joint query+document relevance judgment should prefer - candidate 'b'
    is the only one that actually discusses a liability cap, but is listed
    last, simulating a case a pure embedding similarity ranker could easily
    get wrong (e.g. 'a' and 'c' sharing more surface vocabulary with a
    generic query) while a cross-encoder-style joint judgment gets right."""

    def test_reranking_promotes_the_more_relevant_candidate_over_original_order(self):
        response = _RerankResponse(rankings=[
            _RerankedItem(index=1, relevance_score=0.97),  # 'b' - liability cap, highly relevant
            _RerankedItem(index=0, relevance_score=0.2),
            _RerankedItem(index=2, relevance_score=0.1),
        ])
        fake_llm, structured = _make_fake_llm(response)
        service = RerankerService(fake_llm)

        outcome = service.rerank("liability cap provisions", CANDIDATES, text_key="summary", top_k=3)

        self.assertTrue(outcome.reranked)
        self.assertEqual([r["file_id"] for r in outcome.results], ["b", "a", "c"])
        self.assertEqual(outcome.results[0]["original_rank"], 2, "candidate 'b' was 2nd in the original order")
        self.assertEqual(outcome.results[0]["reranked_rank"], 1, "but is now ranked 1st")
        self.assertEqual(outcome.results[0]["relevance_score"], 0.97)
        structured.invoke.assert_called_once()  # one batched call, not one per candidate

    def test_top_k_truncates_after_reranking_not_before(self):
        response = _RerankResponse(rankings=[
            _RerankedItem(index=2, relevance_score=0.9),
            _RerankedItem(index=1, relevance_score=0.5),
            _RerankedItem(index=0, relevance_score=0.1),
        ])
        fake_llm, _ = _make_fake_llm(response)
        service = RerankerService(fake_llm)

        outcome = service.rerank("query", CANDIDATES, text_key="summary", top_k=2)

        self.assertEqual(len(outcome.results), 2)
        self.assertEqual([r["file_id"] for r in outcome.results], ["c", "b"])


class GracefulDegradationTests(unittest.TestCase):
    def test_llm_exception_falls_back_to_unranked_order_request_still_succeeds(self):
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.side_effect = RuntimeError("simulated Gemini outage")
        fake_llm.with_structured_output.return_value = structured
        service = RerankerService(fake_llm)

        outcome = service.rerank("query", CANDIDATES, text_key="summary", top_k=3)

        self.assertFalse(outcome.reranked)
        self.assertEqual(outcome.reason, "timeout_or_error")
        self.assertEqual([r["file_id"] for r in outcome.results], ["a", "b", "c"], "original order preserved")
        for i, r in enumerate(outcome.results, start=1):
            self.assertEqual(r["original_rank"], i)
            self.assertEqual(r["reranked_rank"], i)
            self.assertIsNone(r["relevance_score"])

    def test_malformed_response_falls_back(self):
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.return_value = {"raw": None, "parsed": None, "parsing_error": ValueError("bad json")}
        fake_llm.with_structured_output.return_value = structured
        service = RerankerService(fake_llm)

        outcome = service.rerank("query", CANDIDATES, text_key="summary", top_k=3)

        self.assertFalse(outcome.reranked)
        self.assertEqual(outcome.reason, "parse_failed")
        self.assertEqual(len(outcome.results), 3)

    def test_no_llm_configured_falls_back(self):
        service = RerankerService(llm=None)

        outcome = service.rerank("query", CANDIDATES, text_key="summary", top_k=3)

        self.assertFalse(outcome.reranked)
        self.assertEqual(outcome.reason, "no_llm_configured")
        self.assertEqual(len(outcome.results), 3)

    def test_empty_candidates_returns_empty_without_calling_the_llm(self):
        fake_llm, structured = _make_fake_llm()
        service = RerankerService(fake_llm)

        outcome = service.rerank("query", [], text_key="summary", top_k=10)

        self.assertFalse(outcome.reranked)
        self.assertEqual(outcome.reason, "no_candidates")
        self.assertEqual(outcome.results, [])
        structured.invoke.assert_not_called()

    def test_hallucinated_out_of_range_index_is_discarded_not_crashed_on(self):
        response = _RerankResponse(rankings=[
            _RerankedItem(index=0, relevance_score=0.9),
            _RerankedItem(index=99, relevance_score=0.99),  # out of range for a 3-candidate list
        ])
        fake_llm, _ = _make_fake_llm(response)
        service = RerankerService(fake_llm)

        outcome = service.rerank("query", CANDIDATES, text_key="summary", top_k=3)

        self.assertTrue(outcome.reranked)
        self.assertEqual(outcome.results[0]["file_id"], "a")
        self.assertEqual({r["file_id"] for r in outcome.results}, {"a", "b", "c"})

    def test_response_missing_some_candidates_keeps_them_in_original_relative_order(self):
        """Model only scored 1 of 3 candidates - the other 2 must still be
        returned (not silently dropped), in their original relative order."""
        response = _RerankResponse(rankings=[_RerankedItem(index=2, relevance_score=0.8)])
        fake_llm, _ = _make_fake_llm(response)
        service = RerankerService(fake_llm)

        outcome = service.rerank("query", CANDIDATES, text_key="summary", top_k=3)

        self.assertEqual([r["file_id"] for r in outcome.results], ["c", "a", "b"])


class TimeoutEnforcementTests(unittest.TestCase):
    def test_slow_call_falls_back_rather_than_hanging(self):
        """Simulates a real timeout the way langchain_google_genai actually
        raises one - as a normal exception from .invoke(), not a distinct
        type - since the client's own request_timeout is what enforces the
        real HTTP-level budget (RERANKER_TIMEOUT_SECONDS), not this test."""
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.side_effect = TimeoutError("simulated request_timeout exceeded")
        fake_llm.with_structured_output.return_value = structured
        service = RerankerService(fake_llm)

        start = time.monotonic()
        outcome = service.rerank("query", CANDIDATES, text_key="summary", top_k=3)
        elapsed = time.monotonic() - start

        self.assertFalse(outcome.reranked)
        self.assertEqual(outcome.reason, "timeout_or_error")
        self.assertEqual(len(outcome.results), 3)
        self.assertLess(elapsed, 1.0, "must fall back immediately, not hang the request")

    def test_reranker_llm_is_constructed_with_a_short_explicit_timeout(self):
        """Real timeout enforcement lives at the LLM client construction
        level (get_reranker_llm), not a wrapper - confirms it's set to
        something short, not the 120s extraction budget."""
        import os
        from backend.agents.reranker_service import get_reranker_llm, RERANKER_TIMEOUT_SECONDS

        self.assertLess(RERANKER_TIMEOUT_SECONDS, 30.0, "must be short - this sits on a synchronous search request")

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key-for-construction-test"}):
            llm = get_reranker_llm()
        self.assertEqual(llm.timeout, RERANKER_TIMEOUT_SECONDS)  # request_timeout is a constructor alias for .timeout
        self.assertEqual(llm.max_retries, 1, "fail fast, matching get_default_llm's identical rationale")


class RerankerCircuitBreakerTests(unittest.TestCase):
    """Same pattern as test_circuit_breaker_wiring.py's LLMExtractionService/
    PolicyEvaluationService classes - fresh, low-threshold breaker patched
    into the module, proving repeated failures actually stop reaching the
    LLM, not just that exceptions get swallowed somewhere."""

    def setUp(self):
        cache.redis_client._cache.clear()
        self.breaker = CircuitBreaker("test_reranker_gemini", failure_threshold=2, recovery_timeout_seconds=60)
        self._patcher = patch("backend.agents.reranker_service.RERANKER_CIRCUIT_BREAKER", self.breaker)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        cache.redis_client._cache.clear()

    def test_repeated_failures_open_the_breaker_and_stop_calling_it(self):
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.side_effect = RuntimeError("simulated Gemini outage")
        fake_llm.with_structured_output.return_value = structured
        service = RerankerService(fake_llm)

        service.rerank("q1", CANDIDATES, text_key="summary", top_k=3)
        service.rerank("q2", CANDIDATES, text_key="summary", top_k=3)  # 2nd failure trips it (threshold=2)
        self.assertEqual(structured.invoke.call_count, 2)
        self.assertEqual(self.breaker.get_status()["state"], "open")

        outcome = service.rerank("q3", CANDIDATES, text_key="summary", top_k=3)

        self.assertEqual(structured.invoke.call_count, 2, "the LLM must not be called again while the breaker is open")
        self.assertFalse(outcome.reranked)
        self.assertEqual(outcome.reason, "circuit_open")
        self.assertEqual(len(outcome.results), 3, "search must still succeed - unranked, not empty/error")

    def test_reranker_breaker_is_independent_of_the_shared_gemini_breaker(self):
        """A dedicated breaker, not a reuse of GEMINI_CIRCUIT_BREAKER - see
        circuit_breaker.py's comment on why sharing would be wrong (a
        stricter timeout tripping the breaker that also guards extraction/
        policy evaluation)."""
        from backend.shared.reliability.circuit_breaker import GEMINI_CIRCUIT_BREAKER, RERANKER_CIRCUIT_BREAKER
        self.assertIsNot(RERANKER_CIRCUIT_BREAKER, GEMINI_CIRCUIT_BREAKER)
        self.assertEqual(RERANKER_CIRCUIT_BREAKER.name, "gemini_reranker")


if __name__ == "__main__":
    unittest.main()
