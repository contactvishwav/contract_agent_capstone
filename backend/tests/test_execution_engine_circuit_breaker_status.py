"""
Regression test for a real gap found while designing the Supervisor Agent
rebuild: ClauseDetectorTool._run catches every exception from
LLMExtractionService.extract_clauses - including CircuitBreakerOpenError -
and returns an empty clause list. Before this fix, PlanExecutionEngine.
_compute_step_status had no way to tell that apart from "this contract
genuinely has zero CUAD clauses" (vanishingly rare for a real contract) -
both looked like an ordinary "success" with 0 results.

_compute_step_status now additionally checks the real, already-built
GEMINI_CIRCUIT_BREAKER's live state, but only when EXTRACT_CLAUSES's own
result came back empty - a genuine successful extraction (non-empty
result) is never second-guessed just because the breaker happens to be
open from an unrelated later call, and no other step type is affected at
all.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.agents.planning.execution_engine import PlanExecutionEngine, ExecutionResult
    from backend.agents.planning.planning_agent import StepType


class ExtractClausesCircuitBreakerStatusTests(unittest.TestCase):
    def setUp(self):
        self.engine = PlanExecutionEngine()

    def _fake_status(self, state):
        return {"name": "gemini", "state": state, "failure_count": 0, "failure_threshold": 5}

    def test_empty_result_with_open_circuit_is_reported_as_failed(self):
        result = ExecutionResult(
            step_id="s1", success=True, output_data=[], execution_time_ms=1, confidence_score=0.9,
        )
        with patch(
            "backend.agents.planning.execution_engine.GEMINI_CIRCUIT_BREAKER.get_status",
            return_value=self._fake_status("open"),
        ):
            status = self.engine._compute_step_status(result, StepType.EXTRACT_CLAUSES)

        self.assertEqual(status, "failed", "an empty extraction while the circuit is open must not read as success")

    def test_empty_result_with_half_open_circuit_is_reported_as_failed(self):
        result = ExecutionResult(
            step_id="s1", success=True, output_data=[], execution_time_ms=1, confidence_score=0.9,
        )
        with patch(
            "backend.agents.planning.execution_engine.GEMINI_CIRCUIT_BREAKER.get_status",
            return_value=self._fake_status("half_open"),
        ):
            status = self.engine._compute_step_status(result, StepType.EXTRACT_CLAUSES)

        self.assertEqual(status, "failed")

    def test_empty_result_with_closed_circuit_is_still_a_genuine_success(self):
        # A real contract with genuinely zero matched CUAD clauses (or a
        # transient non-circuit-breaker reason) must not be misreported as
        # a failure just because the result happened to be empty.
        result = ExecutionResult(
            step_id="s1", success=True, output_data=[], execution_time_ms=1, confidence_score=0.9,
        )
        with patch(
            "backend.agents.planning.execution_engine.GEMINI_CIRCUIT_BREAKER.get_status",
            return_value=self._fake_status("closed"),
        ):
            status = self.engine._compute_step_status(result, StepType.EXTRACT_CLAUSES)

        self.assertEqual(status, "success")

    def test_non_empty_result_is_never_downgraded_even_if_circuit_is_open(self):
        # A genuinely successful extraction must never be second-guessed
        # just because the breaker happens to be open from some unrelated
        # later call.
        result = ExecutionResult(
            step_id="s1", success=True, output_data=[{"clause_type": "Governing Law"}],
            execution_time_ms=1, confidence_score=0.9,
        )
        with patch(
            "backend.agents.planning.execution_engine.GEMINI_CIRCUIT_BREAKER.get_status",
            return_value=self._fake_status("open"),
        ):
            status = self.engine._compute_step_status(result, StepType.EXTRACT_CLAUSES)

        self.assertEqual(status, "success")

    def test_other_step_types_are_unaffected_by_circuit_breaker_state(self):
        # ASSESS_RISK returning an empty dict is a completely different
        # signal (see the dict-based "status" field handling above this
        # check) - it must not be touched by the EXTRACT_CLAUSES-specific
        # circuit breaker override.
        result = ExecutionResult(
            step_id="s1", success=True, output_data={}, execution_time_ms=1, confidence_score=0.9,
        )
        with patch(
            "backend.agents.planning.execution_engine.GEMINI_CIRCUIT_BREAKER.get_status",
            return_value=self._fake_status("open"),
        ):
            status = self.engine._compute_step_status(result, StepType.ASSESS_RISK)

        self.assertEqual(status, "success")

    def test_missing_step_type_falls_back_to_original_binary_behavior(self):
        # Backward compatibility: any existing caller that doesn't pass
        # step_type at all must see the exact original behavior.
        result = ExecutionResult(
            step_id="s1", success=True, output_data=[], execution_time_ms=1, confidence_score=0.9,
        )
        status = self.engine._compute_step_status(result)
        self.assertEqual(status, "success")


if __name__ == "__main__":
    unittest.main()
