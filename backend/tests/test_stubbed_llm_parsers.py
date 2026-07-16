"""
Regression tests documenting known-broken (stubbed) LLM parsing paths.

Three separate code paths build a real LLM prompt, call the LLM, and then
discard the response instead of parsing it:
  - LLMClauseExtractor._parse_llm_response  (backend/agents/clause_extraction_agent.py:91-94)
  - LLMCUADClassifier._parse_llm_response   (backend/agents/cuad_classifier_agent.py:168-171)
  - ClauseDetectorTool._run                 (backend/agents/intelligence_tools.py:90-106,
                                              doesn't even call the LLM - returns
                                              hard-coded clauses regardless of input)

These tests intentionally assert the CURRENT broken behavior (empty / hard-
coded output) so that a future fix is forced to update this file, rather than
silently leaving stale expectations behind.

TODO(Phase 1): implement real JSON parsing of the LLM response in
LLMClauseExtractor._parse_llm_response and LLMCUADClassifier._parse_llm_response,
and make ClauseDetectorTool._run actually invoke the LLM instead of returning
static data. When that lands, replace the assertions below with ones that
verify real parsed output, and remove this TODO.
"""

import json
import unittest
from unittest.mock import MagicMock

from backend.agents.clause_extraction_agent import LLMClauseExtractor
from backend.agents.cuad_classifier_agent import LLMCUADClassifier
from backend.agents.intelligence_tools import ClauseDetectorTool


class TestLLMClauseExtractorStub(unittest.TestCase):
    """TODO(Phase 1): fix LLMClauseExtractor._parse_llm_response to actually parse JSON."""

    def test_parse_llm_response_discards_a_well_formed_response(self):
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(content=json.dumps([
            {"content": "Client shall pay within 30 days.", "type": "obligation", "confidence": 0.9}
        ]))

        extractor = LLMClauseExtractor(fake_llm)
        clauses = extractor.extract_clauses("Some contract section text.", section_id="sec_1")

        # The LLM *was* called with a valid, parseable response...
        fake_llm.invoke.assert_called_once()
        # ...but _parse_llm_response unconditionally returns [], discarding it.
        self.assertEqual(clauses, [])


class TestLLMCUADClassifierStub(unittest.TestCase):
    """TODO(Phase 1): fix LLMCUADClassifier._parse_llm_response to actually parse JSON."""

    def test_parse_llm_response_discards_a_well_formed_response(self):
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(content=json.dumps([
            {"cuad_type": "Governing Law", "confidence": 0.95, "reasoning": "Explicit governing law clause"}
        ]))

        classifier = LLMCUADClassifier(fake_llm)
        clause = {"clause_id": "clause_1", "content": "This agreement is governed by the laws of Delaware."}
        classifications = classifier.classify_clause(clause)

        fake_llm.invoke.assert_called_once()
        self.assertEqual(classifications, [])


class TestClauseDetectorToolStub(unittest.TestCase):
    """TODO(Phase 1): make ClauseDetectorTool._run actually invoke the LLM."""

    def test_output_is_identical_regardless_of_input_text(self):
        tool = ClauseDetectorTool()

        result_a = json.loads(tool._run("Contract A: a totally unrelated confidentiality agreement."))
        result_b = json.loads(tool._run("Contract B: a completely different indemnification-heavy MSA."))

        # Two unrelated contracts produce byte-for-byte identical "extracted"
        # clauses, because _run never inspects contract_text - it returns a
        # hard-coded list regardless of input.
        self.assertEqual(result_a, result_b)
        self.assertEqual(result_a, [
            {
                "clause_type": "Payment Terms",
                "content": "Payment due within 30 days of invoice",
                "risk_level": "LOW",
                "confidence_score": 0.8,
                "location": "Section 3"
            },
            {
                "clause_type": "Liability",
                "content": "Liability limited to $50,000",
                "risk_level": "HIGH",
                "confidence_score": 0.9,
                "location": "Section 8"
            }
        ])


if __name__ == "__main__":
    unittest.main()
