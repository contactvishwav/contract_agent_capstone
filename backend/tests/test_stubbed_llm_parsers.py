"""
Tests for real LLM-based CUAD clause extraction (Phase 1).

Formerly this file documented three broken stubs that discarded the LLM's
response and returned []/hard-coded data:
  - LLMClauseExtractor._parse_llm_response  (backend/agents/clause_extraction_agent.py)
  - LLMCUADClassifier._parse_llm_response   (backend/agents/cuad_classifier_agent.py)
  - ClauseDetectorTool._run                 (backend/agents/intelligence_tools.py)

All three now delegate to LLMExtractionService (backend/agents/
llm_extraction_service.py), which uses Gemini structured output to produce
real, schema-validated ExtractedClause results. These tests assert on that
real behavior using a mocked LLM - no live API calls / no GOOGLE_API_KEY
required to run this file. See research/benchmark/evaluate_extraction.py for
the real-data precision/recall/F1 benchmark against CUAD ground truth.
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.agents.llm_extraction_service import (
    LLMExtractionService,
    CUADClauseType,
    ExtractedClause,
    _LLMExtractedClause,
    _LLMExtractionResponse,
)
from backend.agents.clause_extraction_agent import LLMClauseExtractor, Clause
from backend.agents.cuad_classifier_agent import LLMCUADClassifier, CUADClassification
from backend.agents.intelligence_tools import ClauseDetectorTool

# ClauseDetectorTool._run also writes an audit log entry (P1 item 2) via a
# fresh AuditLogger() per call - unmocked, that constructs a real Neo4j
# connection using whatever graph/embedding singletons are already cached in
# this session, which can attempt a real network round-trip. Patch it out
# here so this file keeps its "no live API calls" guarantee regardless of
# audit logging internals.
_audit_logger_patcher = patch("backend.agents.intelligence_tools.AuditLogger", return_value=MagicMock())
_audit_logger_patcher.start()

# This file tests real extraction behavior for varying inputs, not caching -
# disable the P3-item-20 content-hash cache so it can't return a stale
# result cached under identical text by an earlier test/run.
_cache_disabled_patcher = patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False)
_cache_disabled_patcher.start()


def _wrap_raw(response):
    """
    LLMExtractionService now calls with_structured_output(..., include_raw=
    True), which returns {"raw": AIMessage, "parsed": ..., "parsing_error":
    ...} instead of the parsed object directly - this wraps a fake parsed
    response in that shape.
    """
    return {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 20}),
        "parsed": response,
        "parsing_error": None,
    }


def make_fake_llm(response: _LLMExtractionResponse):
    """
    A fake LangChain chat model exposing just enough of the interface
    LLMExtractionService relies on: with_structured_output(...).invoke(...).
    Returns (fake_llm, structured_mock) so callers can assert on/reconfigure
    the structured mock's invoke() behavior.
    """
    structured = MagicMock()
    structured.invoke.return_value = _wrap_raw(response)
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value = structured
    return fake_llm, structured


class TestLLMExtractionService(unittest.TestCase):
    """Core service: schema call + offset resolution."""

    def test_extract_clauses_returns_real_parsed_results(self):
        source_text = "This Agreement is governed by the laws of the State of Delaware. Other text follows."
        response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.GOVERNING_LAW,
                extracted_text="governed by the laws of the State of Delaware",
                confidence=0.95,
            )
        ])
        fake_llm, structured = make_fake_llm(response)

        service = LLMExtractionService(fake_llm)
        # enable_fallback=False: this test is about parsing/offset
        # resolution, not the FALLBACK_CATEGORIES second-pass behavior
        # (covered separately in test_extraction_fallback_pass.py).
        result = service.extract_clauses(source_text, enable_fallback=False)

        structured.invoke.assert_called_once()
        self.assertEqual(len(result), 1)
        clause = result[0]
        self.assertIsInstance(clause, ExtractedClause)
        self.assertEqual(clause.clause_type, CUADClauseType.GOVERNING_LAW)
        self.assertEqual(clause.confidence, 0.95)

        # Offsets are computed by searching the source text, not hallucinated
        # by the LLM (the LLM-facing schema has no offset fields at all).
        expected_start = source_text.find(clause.extracted_text)
        self.assertNotEqual(expected_start, -1)
        self.assertEqual(clause.start_offset, expected_start)
        self.assertEqual(clause.end_offset, expected_start + len(clause.extracted_text))

    def test_extract_clauses_offset_fallback_for_whitespace_mismatch(self):
        # Extra internal whitespace vs. what the model "recalls" - exact
        # substring search fails, whitespace-insensitive fallback succeeds.
        source_text = "Clause:   Payment   is due   within 30 days of invoice."
        response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.MINIMUM_COMMITMENT,
                extracted_text="Payment is due within 30 days of invoice.",
                confidence=0.8,
            )
        ])
        fake_llm, _ = make_fake_llm(response)

        result = LLMExtractionService(fake_llm).extract_clauses(source_text, enable_fallback=False)

        self.assertEqual(len(result), 1)
        self.assertNotEqual(result[0].start_offset, -1)
        self.assertGreater(result[0].end_offset, result[0].start_offset)

    def test_extract_clauses_returns_empty_without_llm(self):
        self.assertEqual(LLMExtractionService(None).extract_clauses("some contract text"), [])

    def test_extract_clauses_returns_empty_on_llm_error(self):
        fake_llm, structured = make_fake_llm(_LLMExtractionResponse(clauses=[]))
        structured.invoke.side_effect = RuntimeError("API error")

        self.assertEqual(LLMExtractionService(fake_llm).extract_clauses("some contract text"), [])


class TestLLMClauseExtractorRealExtraction(unittest.TestCase):
    def test_extract_clauses_returns_real_clause_objects(self):
        source_text = "Either party may terminate this agreement with 30 days written notice."
        response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.TERMINATION_FOR_CONVENIENCE,
                extracted_text=source_text,
                confidence=0.9,
            )
        ])
        fake_llm, structured = make_fake_llm(response)
        # This call site uses the real default (candidate_types=None,
        # enable_fallback=True) - TERMINATION_FOR_CONVENIENCE isn't in
        # FALLBACK_CATEGORIES, so a second, narrower call fires to check
        # for those. Second canned response is empty (nothing found there).
        structured.invoke.side_effect = [_wrap_raw(response), _wrap_raw(_LLMExtractionResponse(clauses=[]))]

        clauses = LLMClauseExtractor(fake_llm).extract_clauses(source_text, section_id="sec_1")

        self.assertEqual(structured.invoke.call_count, 2, "primary pass + fallback pass for FALLBACK_CATEGORIES")
        self.assertEqual(len(clauses), 1)
        clause = clauses[0]
        self.assertIsInstance(clause, Clause)
        self.assertEqual(clause.clause_type, "Termination For Convenience")
        self.assertEqual(clause.content, source_text)
        self.assertEqual(clause.section_id, "sec_1")
        self.assertGreaterEqual(clause.start_position, 0)


class TestLLMCUADClassifierRealExtraction(unittest.TestCase):
    def test_classify_clause_returns_real_classifications(self):
        response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.GOVERNING_LAW,
                extracted_text="governed by the laws of Delaware",
                confidence=0.92,
            )
        ])
        fake_llm, structured = make_fake_llm(response)

        clause = {"clause_id": "clause_1", "content": "This agreement is governed by the laws of Delaware."}
        classifications = LLMCUADClassifier(fake_llm).classify_clause(clause)

        structured.invoke.assert_called_once()
        self.assertEqual(len(classifications), 1)
        result = classifications[0]
        self.assertIsInstance(result, CUADClassification)
        self.assertEqual(result.clause_id, "clause_1")
        self.assertEqual(result.cuad_type, "Governing Law")
        self.assertEqual(result.detected_by, "llm")

    def test_classify_clause_filters_low_confidence_matches(self):
        response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.GOVERNING_LAW,
                extracted_text="governed by the laws of Delaware",
                confidence=0.5,  # below the > 0.7 threshold
            )
        ])
        fake_llm, _ = make_fake_llm(response)

        clause = {"clause_id": "clause_1", "content": "This agreement is governed by the laws of Delaware."}
        classifications = LLMCUADClassifier(fake_llm).classify_clause(clause)

        self.assertEqual(classifications, [])


class TestClauseDetectorToolRealExtraction(unittest.TestCase):
    def test_run_returns_real_extraction_reflecting_input(self):
        contract_a_text = "Contract A: governed by the laws of California."
        contract_b_text = "Contract B: a completely different indemnification-heavy MSA."

        response_a = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(
                clause_type=CUADClauseType.GOVERNING_LAW,
                extracted_text="governed by the laws of California",
                confidence=0.9,
            )
        ])
        response_b = _LLMExtractionResponse(clauses=[])
        empty = _wrap_raw(_LLMExtractionResponse(clauses=[]))

        fake_llm, structured = make_fake_llm(response_a)
        # This tool's real call site uses the default (candidate_types=None,
        # enable_fallback=True), so each tool._run() below makes a primary
        # call plus a fallback call for FALLBACK_CATEGORIES (empty in both
        # cases here, since neither example touches those 8 rare types).
        structured.invoke.side_effect = [_wrap_raw(response_a), empty, _wrap_raw(response_b), empty]

        tool = ClauseDetectorTool(llm=fake_llm)
        result_a = json.loads(tool._run(contract_a_text))
        result_b = json.loads(tool._run(contract_b_text))

        # Unlike the old stub, output now genuinely differs based on input.
        self.assertNotEqual(result_a, result_b)
        self.assertEqual(len(result_a), 1)
        self.assertEqual(result_a[0]["clause_type"], "Governing Law")
        self.assertEqual(result_a[0]["content"], "governed by the laws of California")
        self.assertEqual(result_b, [])

    def test_run_returns_empty_list_without_llm_or_api_key(self):
        # No llm passed in, and get_default_llm() returns None unless a real
        # key is configured in this process's environment.
        if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
            self.skipTest("A real API key is configured in this environment")

        result = json.loads(ClauseDetectorTool()._run("Some contract text."))
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
