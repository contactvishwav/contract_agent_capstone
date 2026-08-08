"""
Regression tests for a chain of real, confirmed bugs found live while
verifying the POST /api/policies/upload asyncio.run()-in-a-running-loop
fix (see PolicyChunkingAgentRunningEventLoopRegressionTests in
test_policy_chunking.py). Fixing that bug let requests reach further into
the pipeline, which is exactly how these were found - each one was
masking the next:

1. infrastructure/policy_validation_service.py's PolicyContentValidator
   called ContentValidationService.validate_file_upload({'content': ...,
   'file_type': 'policy', ...}) - a method whose own docstring says
   "filename, size only". Its FileTypeValidator unconditionally requires
   a `.pdf` filename that a raw-text policy upload never has, so it
   always failed - masked by a second bug in the same block:
   `validation_result.get('valid', False)` checked a key that doesn't
   exist (the real key is 'is_valid'), so the check always fell through
   to `False` regardless. Net effect: every policy upload failed content
   validation, always, regardless of content.
2. Once (1) was fixed, PolicyValidationService's chain wiring surfaced:
   structure_validator was chained to content_validator AND rule_validator
   together, but rule_validator validates `extracted_rules`, which only
   exists after extraction has run - validate_policy_upload (called
   before extraction) always cascaded into it with an empty list and
   failed with "No policy rules could be extracted". Invisible until (1)
   was fixed, because content_validator always failed first.
3. application/services/policy_service.py's two ErrorTracker.track_error()
   calls used keyword arguments (error_type=, error_message=) that don't
   exist on the real method (error=, category=, severity=, context=) -
   a TypeError raised from inside an except block, masking whatever the
   original error actually was and turning a clean validation-failure
   response into an unhandled 500.
4. agents/policy_agents.py's PolicyExtractionAgent.execute() returned a
   result dict with no `document_id` - PolicyWorkflowOrchestrator.
   process_policy_document's `final_result` is just the last step's data,
   so a fully successful upload still reported `policy_id: None`.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.policy_validation_service import PolicyValidationService
    from backend.agents.supervisor.interfaces import AgentContext

SAMPLE_POLICY = """
1. LIABILITY POLICY

The Company shall not accept unlimited liability in any contract.
All contracts must include a liability cap of $1,000,000.
Indemnification clauses are prohibited unless approved by legal.

2. TERMINATION POLICY

All contracts must include a 30-day notice period for termination.
Immediate termination is prohibited except for material breach.
Termination clauses should specify post-termination obligations.
"""


class PolicyContentValidatorTests(unittest.TestCase):
    """Bug 1: content validation must not require a filename, and must
    actually read the real is_valid/results shape."""

    def test_real_policy_text_passes_without_a_filename(self):
        service = PolicyValidationService()
        result = service.validate_policy_upload({
            "policy_text": SAMPLE_POLICY,
            "tenant_id": "tenant_a",
            "policy_name": "Test Liability Policy",
        })
        self.assertTrue(result.passed, result.message)

    def test_short_garbled_content_still_fails_content_quality(self):
        # Control: confirms the fix isn't just disabling content
        # validation outright - genuinely too-short content must still
        # be rejected, just for the real reason (length), not a phantom
        # missing-filename error.
        service = PolicyValidationService()
        result = service.validate_policy_upload({
            "policy_text": "shall must " * 3,  # has policy keywords, but far under 100 chars
            "tenant_id": "tenant_a",
            "policy_name": "Too Short Policy",
        })
        self.assertFalse(result.passed)
        self.assertIn("too short", result.message.lower())


class PolicyValidationChainWiringTests(unittest.TestCase):
    """Bug 2: validate_policy_upload must not cascade into rule_validator,
    which depends on extracted_rules that doesn't exist pre-extraction."""

    def test_valid_policy_upload_does_not_require_extracted_rules(self):
        service = PolicyValidationService()
        policy_data = {
            "policy_text": SAMPLE_POLICY,
            "tenant_id": "tenant_a",
            "policy_name": "Test Liability Policy",
        }
        self.assertNotIn("extracted_rules", policy_data)

        result = service.validate_policy_upload(policy_data)

        self.assertTrue(result.passed, result.message)
        self.assertNotIn("No policy rules could be extracted", result.message)

    def test_rule_validator_still_works_standalone_for_real_extracted_rules(self):
        # Control: rule_validator itself isn't broken, it's just wired
        # into the wrong chain - validate_policy_rules (its real,
        # standalone entry point) must still work correctly.
        service = PolicyValidationService()
        good_rules = [
            {"rule_text": "Contracts must include a liability cap.", "rule_type": "mandatory"},
            {"rule_text": "Unlimited liability is prohibited in all agreements.", "rule_type": "prohibited"},
        ]
        result = service.validate_policy_rules({"policy_text": SAMPLE_POLICY}, good_rules)
        self.assertTrue(result.passed, result.message)


class PolicyServiceErrorTrackingTests(unittest.IsolatedAsyncioTestCase):
    """Bug 3: track_error() must be called with its real kwargs, or a
    validation failure turns into an unhandled TypeError instead of a
    clean {'success': False, 'error': ...} response."""

    async def test_validation_failure_returns_clean_error_without_raising(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.application.services.policy_service import PolicyService

        service = PolicyService()
        # Real ErrorTracker.__init__ builds a Neo4jContractRepository - not
        # needed here, and irrelevant to what this test checks (that
        # track_error's kwargs are valid), so stub it out.
        service.audit_service.error_tracker = MagicMock()

        # Too-short content -> real validation failure -> should hit the
        # track_error call this test is guarding, not raise from it.
        result = await service.upload_and_process_policy({
            "policy_text": "too short",
            "tenant_id": "tenant_a",
            "policy_name": "Too Short Policy",
        })

        self.assertFalse(result["success"])
        self.assertIn("too short", result["error"].lower())
        service.audit_service.error_tracker.track_error.assert_called_once()
        # The real signature - if these kwargs were wrong, the call above
        # would have raised TypeError instead of being recorded here.
        _, call_kwargs = service.audit_service.error_tracker.track_error.call_args
        self.assertIn("error", call_kwargs)
        self.assertIn("category", call_kwargs)
        self.assertIn("severity", call_kwargs)
        self.assertIn("context", call_kwargs)


class PolicyUploadEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Bug 4 + full pipeline: a real policy document must be reported as
    both successful AND carry a usable policy_id."""

    async def test_successful_upload_returns_a_real_policy_id(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.application.services.policy_service import PolicyService

        service = PolicyService()
        service.orchestrator.registry.get_agent("policy_chunking").storage_service.store_chunks = AsyncMock(
            return_value={"chunks_stored": 3}
        )
        service.audit_service.error_tracker = MagicMock()
        service.audit_service.log_policy_upload = MagicMock()
        service.audit_service.log_policy_processing = MagicMock()
        service.cache_service.invalidate_policy_cache = MagicMock()

        with patch("backend.agents.policy_agents.graph"):
            result = await service.upload_and_process_policy({
                "policy_text": SAMPLE_POLICY,
                "tenant_id": "tenant_a",
                "policy_name": "Test Liability Policy",
                "policy_type": "compliance",
                "version": "1.0",
            })

        self.assertTrue(result["success"], result.get("error"))
        self.assertIsNotNone(result["policy_id"])
        self.assertTrue(result["policy_id"].startswith("policy_tenant_a_"))
        service.audit_service.error_tracker.track_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
