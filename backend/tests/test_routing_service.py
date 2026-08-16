"""Phase 6 (MLOps governance): deterministic student/teacher chat routing.

classify_complexity()/route_chat_model() are pure functions with no
provider/network dependency, so these are plain unit tests - no app/Neo4j/
Redis mocking needed, unlike most of this test suite."""

import unittest

from backend.model_registry import MODEL_SPECS
from backend.routing_service import (
    STUDENT_MODEL_ID,
    TEACHER_MODEL_ID,
    classify_complexity,
    route_chat_model,
)

_REGISTRY_IDS = {spec.stable_id for spec in MODEL_SPECS}


class TestRoutingService(unittest.TestCase):
    def test_router_ids_are_real_registry_entries(self):
        """The router must only ever point at ids model_registry.py
        actually knows about - a typo'd constant here would silently 503
        every auto-routed request at validate_model()."""
        self.assertIn(STUDENT_MODEL_ID, _REGISTRY_IDS)
        self.assertIn(TEACHER_MODEL_ID, _REGISTRY_IDS)

    def test_simple_extraction_prompts_route_to_student(self):
        prompts = [
            "What is the termination notice period in this contract?",
            "List the parties to this agreement.",
            "What is the effective date?",
            "Extract the payment terms from this MSA.",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(classify_complexity(prompt), "student")

    def test_redline_and_synthesis_prompts_route_to_teacher(self):
        prompts = [
            "Please redline this MSA to cap our liability at 12 months fees.",
            "Synthesize the indemnification positions across all uploaded contracts.",
            "Draft a counterproposal for the limitation of liability clause.",
            "What's our negotiation strategy for the payment terms in this SOW?",
            "Do a full risk assessment and compare the termination clauses across both agreements.",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(classify_complexity(prompt), "teacher")

    def test_long_compound_prompt_routes_to_teacher_without_keyword(self):
        long_prompt = " ".join(["word"] * 61)
        self.assertEqual(classify_complexity(long_prompt), "teacher")

    def test_short_prompt_without_signal_routes_to_student(self):
        short_prompt = " ".join(["word"] * 10)
        self.assertEqual(classify_complexity(short_prompt), "student")

    def test_empty_prompt_routes_to_student(self):
        self.assertEqual(classify_complexity(""), "student")
        self.assertEqual(classify_complexity("   "), "student")

    def test_route_chat_model_returns_matching_pair(self):
        model_id, tier = route_chat_model("What is the governing law clause?")
        self.assertEqual((model_id, tier), (STUDENT_MODEL_ID, "student"))

        model_id, tier = route_chat_model("Redline this NDA for a mutual cap on liability.")
        self.assertEqual((model_id, tier), (TEACHER_MODEL_ID, "teacher"))


if __name__ == "__main__":
    unittest.main()
