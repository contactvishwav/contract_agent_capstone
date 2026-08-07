"""
Regression test for AI-engineering-depth audit finding #11: PROMPT_VERSION
in llm_extraction_service.py and policy_evaluation_service.py stayed "v1"
since introduction despite _build_prompt's actual wording changing across
several commits - decorative versioning that never busted the cache on
those changes, since the cache key is content + PROMPT_VERSION, not
content alone.

Enforcement mechanism: this test hashes each _build_prompt method's real
source and compares it against a table of expected hashes keyed by that
file's current PROMPT_VERSION.

- Change the prompt's wording without bumping PROMPT_VERSION: the hash for
  the *current* (unchanged) version no longer matches -> fails, with a
  message telling you to bump the version.
- Bump PROMPT_VERSION without recording a new expected hash here: there's
  no entry for the new version -> also fails, forcing you to touch this
  file and consciously record what the new prompt actually hashes to.

The only way to make this test pass after touching either prompt is to do
both: bump PROMPT_VERSION *and* add its real new hash below - which is
exactly the coupling that was missing before.
"""

import hashlib
import inspect
import unittest

from backend.agents import llm_extraction_service, policy_evaluation_service

_EXPECTED_EXTRACTION_PROMPT_HASHES = {
    "v2": "96a11b4f2007b701080ed70e1d72b5e64176f420de18f5ce7c02067e02ba54ad",
    "v3": "ee7751236b8bda18b8b6f183cfecd4ff8bd50864104ad7984dc6725053abf38b",
}

_EXPECTED_POLICY_PROMPT_HASHES = {
    "v2": "d7869e1a3810e64c1f7923f8d774efcd77696f4107374ad13bd824079c49001e",
}


def _source_hash(func) -> str:
    return hashlib.sha256(inspect.getsource(func).encode()).hexdigest()


class PromptVersionBumpEnforcementTests(unittest.TestCase):
    def test_extraction_prompt_source_matches_its_declared_version(self):
        current_version = llm_extraction_service.PROMPT_VERSION
        expected_hash = _EXPECTED_EXTRACTION_PROMPT_HASHES.get(current_version)
        self.assertIsNotNone(
            expected_hash,
            f"llm_extraction_service.PROMPT_VERSION={current_version!r} has no "
            "recorded expected hash in this test. If you just bumped "
            "PROMPT_VERSION, add its real _build_prompt source hash to "
            "_EXPECTED_EXTRACTION_PROMPT_HASHES.",
        )
        actual_hash = _source_hash(llm_extraction_service.LLMExtractionService._build_prompt)
        self.assertEqual(
            actual_hash, expected_hash,
            "LLMExtractionService._build_prompt's source changed but "
            "PROMPT_VERSION was not bumped (cached extraction results from "
            "the old prompt would be silently reused under the new one). "
            "Bump PROMPT_VERSION and record the new hash in this test.",
        )

    def test_policy_prompt_source_matches_its_declared_version(self):
        current_version = policy_evaluation_service.PROMPT_VERSION
        expected_hash = _EXPECTED_POLICY_PROMPT_HASHES.get(current_version)
        self.assertIsNotNone(
            expected_hash,
            f"policy_evaluation_service.PROMPT_VERSION={current_version!r} has "
            "no recorded expected hash in this test. If you just bumped "
            "PROMPT_VERSION, add its real _build_prompt source hash to "
            "_EXPECTED_POLICY_PROMPT_HASHES.",
        )
        actual_hash = _source_hash(policy_evaluation_service.PolicyEvaluationService._build_prompt)
        self.assertEqual(
            actual_hash, expected_hash,
            "PolicyEvaluationService._build_prompt's source changed but "
            "PROMPT_VERSION was not bumped (cached evaluation results from "
            "the old prompt would be silently reused under the new one). "
            "Bump PROMPT_VERSION and record the new hash in this test.",
        )

    def test_both_prompts_are_no_longer_stuck_on_v1(self):
        """The concrete regression this finding was about: PROMPT_VERSION
        had never moved off its initial value despite real prompt changes
        since introduction."""
        self.assertNotEqual(llm_extraction_service.PROMPT_VERSION, "v1")
        self.assertNotEqual(policy_evaluation_service.PROMPT_VERSION, "v1")


if __name__ == "__main__":
    unittest.main()
