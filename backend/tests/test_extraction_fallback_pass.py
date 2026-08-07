"""
Regression tests for the weak-category accuracy pass (docs/EVALUATION.md):
20 of the 36 risk-relevant CUAD categories were scoring below 0.30 F1 in the
497-contract flash-lite benchmark. Root-cause analysis of the real
checkpoint predictions (research/benchmark/analysis/root_cause_weak_categories.py)
split them into three groups, each getting a different, targeted fix:

  - Group A (Third Party Beneficiary, Change Of Control): the model was
    finding *a* clause but the wrong one (a disclaimer instead of a grant;
    a definition instead of the operative clause) - fixed with a one-line
    prompt hint disambiguating what the category actually means.
  - Group C (10 categories): ordinary recall gaps where the model already
    finds some real examples but under-recalls - same one-line-hint
    treatment.
  - Group B (8 categories: FALLBACK_CATEGORIES): the model was essentially
    never attempting these at all (0-1 true positives across 497 contracts,
    near-zero false positives too - not wrong guesses, no guesses). These
    get a second, narrower, example-backed LLM call, fired only when the
    primary 41-category pass found none of them - most real contracts
    genuinely don't contain these rare clause types, so this keeps the
    common case at one LLM call while still giving the rare-but-present
    case a real, focused second look.

These tests use the same make_fake_llm/CountingFakeLLM patterns as
test_stubbed_llm_parsers.py / test_llm_extraction_caching.py - no live API
calls, no GOOGLE_API_KEY required.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.agents.llm_extraction_service import (
    LLMExtractionService,
    CUADClauseType,
    FALLBACK_CATEGORIES,
    _LLMExtractedClause,
    _LLMExtractionResponse,
)


def _wrap_raw(response):
    return {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 20}),
        "parsed": response,
        "parsing_error": None,
    }


def make_fake_llm(*responses):
    """Fake LLM whose structured invoke() returns each response in sequence."""
    structured = MagicMock()
    structured.invoke.side_effect = [_wrap_raw(r) for r in responses]
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value = structured
    return fake_llm, structured


_EMPTY = _LLMExtractionResponse(clauses=[])


class CategoryHintPromptTests(unittest.TestCase):
    """Group A/C: hints must be present in the main 41-category prompt,
    and must NOT be sprinkled onto categories that were already scoring
    well (dilution risk explicitly flagged before implementation)."""

    def test_third_party_beneficiary_hint_warns_against_disclaimer_clauses(self):
        service = LLMExtractionService(llm=None)
        prompt = service._build_prompt("some contract text", None)
        self.assertIn("Third Party Beneficiary: only a clause that GRANTS", prompt)
        self.assertIn("do NOT match boilerplate", prompt)

    def test_change_of_control_hint_prefers_operative_clause_over_definition(self):
        service = LLMExtractionService(llm=None)
        prompt = service._build_prompt("some contract text", None)
        self.assertIn("Change Of Control: the operative clause", prompt)
        self.assertIn("not a bare definition", prompt)

    def test_all_twenty_weak_categories_have_guidance_somewhere(self):
        """Group A + Group C (12) get an inline hint; Group B (8) gets the
        fallback pass instead - together that's all 20 categories from the
        docs/EVALUATION.md weak-category table."""
        from backend.agents.llm_extraction_service import _CATEGORY_HINTS

        group_a_and_c = {
            CUADClauseType.THIRD_PARTY_BENEFICIARY, CUADClauseType.CHANGE_OF_CONTROL,
            CUADClauseType.NON_DISPARAGEMENT, CUADClauseType.MOST_FAVORED_NATION,
            CUADClauseType.SOURCE_CODE_ESCROW, CUADClauseType.ROFR_ROFO_ROFN,
            CUADClauseType.IP_OWNERSHIP_ASSIGNMENT, CUADClauseType.REVENUE_PROFIT_SHARING,
            CUADClauseType.MINIMUM_COMMITMENT, CUADClauseType.EXCLUSIVITY,
            CUADClauseType.UNCAPPED_LIABILITY, CUADClauseType.PRICE_RESTRICTIONS,
        }
        self.assertEqual(set(_CATEGORY_HINTS.keys()), group_a_and_c)
        self.assertEqual(len(group_a_and_c) + len(FALLBACK_CATEGORIES), 20)
        self.assertEqual(set(FALLBACK_CATEGORIES) & group_a_and_c, set())

    def test_already_strong_categories_get_no_hint(self):
        """Dilution guard explicitly requested before implementation: the 5
        metadata fields already at 0.75+ F1 must stay exactly as before."""
        from backend.agents.llm_extraction_service import _CATEGORY_HINTS

        for t in (
            CUADClauseType.DOCUMENT_NAME, CUADClauseType.PARTIES,
            CUADClauseType.AGREEMENT_DATE, CUADClauseType.EFFECTIVE_DATE,
            CUADClauseType.EXPIRATION_DATE, CUADClauseType.GOVERNING_LAW,
        ):
            self.assertNotIn(t, _CATEGORY_HINTS)
            self.assertNotIn(t, FALLBACK_CATEGORIES)


class FallbackPassTriggerTests(unittest.TestCase):
    def setUp(self):
        cache_patcher = patch("backend.agents.llm_extraction_service.Phase3Config.CACHE_ENABLED", False)
        cache_patcher.start()
        self.addCleanup(cache_patcher.stop)

    def test_fallback_pass_fires_when_primary_pass_finds_none_of_the_eight(self):
        primary_response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type=CUADClauseType.GOVERNING_LAW, extracted_text="Delaware law applies.", confidence=0.9),
        ])
        fallback_response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type=CUADClauseType.VOLUME_RESTRICTION, extracted_text="No more than 500 units per month.", confidence=0.85),
        ])
        fake_llm, structured = make_fake_llm(primary_response, fallback_response)

        result = LLMExtractionService(fake_llm).extract_clauses("Delaware law applies. No more than 500 units per month.")

        self.assertEqual(structured.invoke.call_count, 2)
        types_found = {c.clause_type for c in result}
        self.assertIn(CUADClauseType.GOVERNING_LAW, types_found)
        self.assertIn(CUADClauseType.VOLUME_RESTRICTION, types_found)

    def test_fallback_pass_prompt_only_lists_the_eight_rare_categories(self):
        service = LLMExtractionService(llm=None)
        prompt = service._build_fallback_prompt("some text", FALLBACK_CATEGORIES)
        for t in FALLBACK_CATEGORIES:
            self.assertIn(t.value, prompt)
        # Must not balloon back out to the full 41-category list.
        self.assertNotIn(CUADClauseType.GOVERNING_LAW.value, prompt)
        self.assertNotIn(CUADClauseType.DOCUMENT_NAME.value, prompt)

    def test_fallback_pass_skipped_when_primary_pass_already_found_all_eight(self):
        primary_response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type=t, extracted_text=f"clause for {t.value}", confidence=0.9)
            for t in FALLBACK_CATEGORIES
        ])
        fake_llm, structured = make_fake_llm(primary_response)

        result = LLMExtractionService(fake_llm).extract_clauses("contract text covering all eight already")

        self.assertEqual(structured.invoke.call_count, 1, "no fallback call needed - primary pass already covered all 8")
        self.assertEqual(len(result), 8)

    def test_fallback_pass_only_fires_for_full_extraction_not_narrowed_candidate_types(self):
        primary_response = _LLMExtractionResponse(clauses=[])
        fake_llm, structured = make_fake_llm(primary_response)

        LLMExtractionService(fake_llm).extract_clauses(
            "some text", candidate_types=[CUADClauseType.GOVERNING_LAW]
        )

        self.assertEqual(structured.invoke.call_count, 1, "a caller-narrowed candidate_types must not trigger the fallback pass")

    def test_enable_fallback_false_disables_the_second_call(self):
        primary_response = _LLMExtractionResponse(clauses=[])
        fake_llm, structured = make_fake_llm(primary_response)

        LLMExtractionService(fake_llm).extract_clauses("some text", enable_fallback=False)

        self.assertEqual(structured.invoke.call_count, 1)

    def test_fallback_pass_failure_degrades_to_primary_result_not_a_crash(self):
        primary_response = _LLMExtractionResponse(clauses=[
            _LLMExtractedClause(clause_type=CUADClauseType.GOVERNING_LAW, extracted_text="Delaware law.", confidence=0.9),
        ])
        fake_llm = MagicMock()
        structured = MagicMock()
        structured.invoke.side_effect = [_wrap_raw(primary_response), RuntimeError("quota exceeded")]
        fake_llm.with_structured_output.return_value = structured

        result = LLMExtractionService(fake_llm).extract_clauses("Delaware law.", raise_on_error=True)

        self.assertEqual(len(result), 1, "primary result must survive even if the fallback call blows up")
        self.assertEqual(result[0].clause_type, CUADClauseType.GOVERNING_LAW)


class FallbackPassCachingTests(unittest.TestCase):
    def setUp(self):
        cache_patcher = patch("backend.agents.llm_extraction_service.Phase3Config.CACHE_ENABLED", True)
        cache_patcher.start()
        self.addCleanup(cache_patcher.stop)
        from backend.shared.cache.redis_cache import cache
        self.cache = cache
        self.cache.redis_client._cache.clear()
        self.addCleanup(self.cache.redis_client._cache.clear)

    def test_enable_fallback_true_and_false_are_different_cache_entries(self):
        response = _LLMExtractionResponse(clauses=[])
        fake_llm, structured = make_fake_llm(response, response, response)
        service = LLMExtractionService(fake_llm)
        text = "Same text, different enable_fallback."

        service.extract_clauses(text, enable_fallback=False)
        service.extract_clauses(text, enable_fallback=True)

        self.assertEqual(structured.invoke.call_count, 3, "enable_fallback=False (1 call) then True (primary + fallback) must not share a cache entry")


if __name__ == "__main__":
    unittest.main()
