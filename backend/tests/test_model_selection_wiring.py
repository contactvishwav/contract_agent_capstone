"""
Item 4: the "AI Model" dropdown's selection never reached the model that
actually served a real request, on either of the two paths that matter.

Bug A - upload route ignored the real field entirely: document_upload.py's
/upload route declared `model: str = Query(...)`, but DocumentUpload.tsx
sends `model` as a multipart form field (formData.append('model', ...)),
never as a URL query parameter. Query() only binds query-string params, so
`model` silently resolved to the "gemini-2.5-flash" default on every real
upload, regardless of what the dropdown said - confirmed by the fact that
document_processing_service.py's process_pdf_upload *does* correctly
resolve a real, distinct LLM per model name once it receives one; the
value just never arrived. Fixed by binding it via Form(...) instead.

Bug B - analyze path resolved the selection, then discarded it: contract_
intelligence_service.py's analyze_contract_intelligence correctly resolves
model -> a real llm instance (_get_llm_for_model) and passes it into
IntelligenceOrchestrator(llm). But both real orchestration paths then
constructed ClauseDetectorTool()/PolicyCheckerTool() with *no* llm
argument - IntelligenceOrchestrator._extract_clauses/_check_policies (the
traditional LangGraph path) and StepExecutor.__init__ (the planning/
PlanExecutionEngine path, which is the actual production default,
use_planning=True in the /analyze route). Per the LLM multi-provider
fallback build, a tool constructed with no explicit llm falls back to the
Gemini->OpenAI->Anthropic chain - so every real analysis used the fallback
chain's primary (Gemini) regardless of the user's real selection. Fixed by
threading llm through IntelligenceOrchestrator -> PlanExecutionEngine ->
StepExecutor -> ClauseDetectorTool/PolicyCheckerTool (RiskCalculatorTool/
RedlineGeneratorTool are deterministic, no LLM involved, nothing to fix).

Proven here via LLMUsageTracker's own model dimension (record_call's
model_used arg) - the same mechanism the fallback build already uses to
distinguish "which model really served this request" - rather than just
asserting on constructor wiring, per instruction to prove this end to end.

Bug C - found live, only after fixing bug B: with the selected llm
actually reaching a real call, every real analysis started failing -
"'CompiledStateGraph' object has no attribute 'with_structured_output'".
contract_intelligence_service.py's _get_llm_for_model tried
self.llm_manager.agents[model]._llm, hoping to unwrap a raw chat model
out of LLMManager.agents - but llm_manager.py's get_agent(llm) returns
builder.compile(), a bare CompiledStateGraph (the Contract Chat
tool-calling agent), which never had a `._llm` attribute at all. hasattr(
...) was always False, for every model - so this always silently
returned the compiled graph itself, for every model, not just non-default
ones, invisible until bug B's fix made the resolved value actually reach
a real with_structured_output() call. Fixed by having LLMManager keep a
second dict, raw_llms, holding the actual chat model instance each agent
was built from, and having _get_llm_for_model read from that instead.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Bug A: upload route binds `model` from the real multipart field
# ---------------------------------------------------------------------------

class UploadRouteModelFieldBindingTests(unittest.TestCase):
    """Real HTTP-level test (FastAPI TestClient, real multipart parsing) -
    a direct Python call to upload_pdf(...) (as other tests in this suite
    do) would never exercise Query()-vs-Form() binding at all, since that
    only matters through FastAPI's actual request-parsing layer."""

    def test_model_sent_as_multipart_form_field_reaches_processing_options(self):
        import io
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.api import document_upload
            from backend.governance.auth import get_current_identity, TokenIdentity

        app = FastAPI()
        app.include_router(document_upload.router)

        fake_identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN", username="tester")
        app.dependency_overrides[get_current_identity] = lambda: fake_identity

        fake_llm_mgr = MagicMock()
        fake_llm_mgr.agents = {"gemini-2.5-flash": MagicMock(), "gpt-4o": MagicMock()}
        fake_llm_mgr.raw_llms = {"gemini-2.5-flash": MagicMock(), "gpt-4o": MagicMock()}
        app.state.llm_manager = fake_llm_mgr

        fake_repo = MagicMock()
        fake_repo.graph.query.side_effect = lambda cypher, params=None: (
            [{"contract_id": "UPLOADED_TEST_20260808"}] if "SET c.source_hash" in cypher else []
        )

        captured = {}

        async def fake_process_pdf_upload(processing_request):
            captured["model"] = processing_request.processing_options.get("model")
            return {
                "status": "success", "contract_id": "UPLOADED_TEST_20260808",
                "final_result": "Contract stored successfully",
            }

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only-placeholder"}), \
             patch("backend.infrastructure.audit_logger.AuditLogger.log_event"), \
             patch("backend.infrastructure.contract_repository.Neo4jContractRepository", return_value=fake_repo), \
             patch("backend.infrastructure.text_extractors.extract_pages_async", new=AsyncMock(return_value=SimpleNamespace(full_text="Contract text " * 50, pages=[]))), \
             patch("backend.application.services.pdf_provenance_service.PdfProvenanceService"), \
             patch("backend.agents.chunking_agent.ChunkingAgent.process_document",
                   new=AsyncMock(return_value={"success": False})), \
             patch("backend.application.services.document_processing_service.DocumentServiceFactory.create_service") as fake_factory:
            fake_service = MagicMock()
            fake_service.process_pdf_upload = AsyncMock(side_effect=fake_process_pdf_upload)
            fake_factory.return_value = fake_service

            client = TestClient(app)
            response = client.post(
                "/api/documents/upload",
                files={"file": ("Test_Contract.pdf", io.BytesIO(b"%PDF-1.4 fake pdf content"), "application/pdf")},
                # The real shape DocumentUpload.tsx sends: a multipart form
                # field, not a query string (`?model=...`).
                data={"model": "gpt-4o"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            captured.get("model"), "gpt-4o",
            "the real multipart 'model' field must reach processing_options - "
            "if this is 'gemini-2.5-flash' instead, the route regressed back to Query()",
        )


# ---------------------------------------------------------------------------
# Bug B: analyze path threads the resolved llm all the way to the real call
# ---------------------------------------------------------------------------

def _fake_llm(model_name: str, clauses_response):
    """Minimal fake matching LLMExtractionService/PolicyEvaluationService's
    with_structured_output(..., include_raw=True).invoke(...) contract -
    same shape test_stubbed_llm_parsers.py's make_fake_llm uses, built
    independently here so this file has no import-time coupling to that
    file's module-level patches."""
    structured = MagicMock()
    structured.invoke.return_value = {
        "raw": SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}),
        "parsed": clauses_response,
        "parsing_error": None,
    }
    fake = MagicMock()
    fake.model = model_name
    fake.with_structured_output.return_value = structured
    return fake


class StepExecutorModelSelectionTests(unittest.TestCase):
    """The planning path (StepExecutor) - the real production default,
    use_planning=True in the /analyze route."""

    def _extract_with(self, model_name: str):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.llm_extraction_service import _LLMExtractionResponse
            from backend.agents.planning.execution_engine import StepExecutor
            from backend.agents.planning.planning_agent import StepType

        fake_llm = _fake_llm(model_name, _LLMExtractionResponse(clauses=[]))
        executor = StepExecutor(fake_llm)
        tool = executor.tools[StepType.EXTRACT_CLAUSES]

        with patch("backend.agents.intelligence_tools.AuditLogger", return_value=MagicMock()), \
             patch("backend.shared.config.phase3_config.Phase3Config.CACHE_ENABLED", False), \
             patch("backend.agents.llm_extraction_service.llm_usage_tracker") as fake_tracker:
            tool._run("Some contract text about payment and liability.", contract_id="c1", tenant_id="t1")

        record_calls = fake_tracker.record_call.call_args_list
        self.assertTrue(record_calls, "LLMExtractionService must record real usage via llm_usage_tracker")
        # record_call("clause_extraction", model_used, cache_hit=..., ...) -
        # model is the second positional arg.
        return record_calls[-1].args[1]

    def test_first_model_selection_is_the_one_actually_recorded(self):
        model_used = self._extract_with("gpt-4o")
        self.assertEqual(model_used, "gpt-4o")

    def test_a_different_model_selection_is_reflected_too(self):
        """Regression for the real bug: before the fix, StepExecutor built
        ClauseDetectorTool() with no llm at all, so this would always
        route through the fallback chain (Gemini first) - two different
        selections would never actually differ in what got recorded."""
        model_a = self._extract_with("gpt-4o")
        model_b = self._extract_with("claude-sonnet-5")

        self.assertEqual(model_a, "gpt-4o")
        self.assertEqual(model_b, "claude-sonnet-5")
        self.assertNotEqual(model_a, model_b)


class OrchestratorThreadsLlmToBothPathsTests(unittest.TestCase):
    """Wiring-level proof for both real orchestration paths at once -
    IntelligenceOrchestrator must pass the same resolved llm to its own
    traditional-path tools AND to PlanExecutionEngine/StepExecutor,
    not just store it on self.llm and never read it again."""

    def test_traditional_path_tools_receive_the_resolved_llm(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"), \
             patch("backend.agents.planning.planning_agent.PlanningAgentFactory.create_planning_agent"):
            from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator

        fake_llm = _fake_llm("gpt-4o", None)
        with patch("backend.agents.contract_intelligence_agents._get_redis_checkpointer", return_value=None):
            orchestrator = IntelligenceOrchestrator(fake_llm)

        with patch("backend.agents.contract_intelligence_agents.ClauseDetectorTool") as MockClauseTool, \
             patch("backend.agents.contract_intelligence_agents.workflow_tracker"):
            MockClauseTool.return_value._run.side_effect = Exception("stop after construction")
            orchestrator._extract_clauses({"contract_text": "x", "node_status": {}})

        MockClauseTool.assert_called_once_with(fake_llm)

    def test_planning_path_execution_engine_receives_the_resolved_llm(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator

        fake_llm = _fake_llm("claude-sonnet-5", None)

        with patch("backend.agents.planning.planning_agent.PlanningAgentFactory.create_planning_agent"), \
             patch("backend.agents.contract_intelligence_agents.PlanExecutionEngine") as MockEngine, \
             patch("backend.agents.contract_intelligence_agents._get_redis_checkpointer", return_value=None):
            IntelligenceOrchestrator(fake_llm)

        MockEngine.assert_called_once_with(fake_llm)


# ---------------------------------------------------------------------------
# Bug C: _get_llm_for_model must return a real chat model, never the
# compiled Contract Chat agent LLMManager.agents actually stores
# ---------------------------------------------------------------------------

class GetLlmForModelReturnsRealLlmTests(unittest.TestCase):
    """Regression for the real bug found live only after fixing bug B:
    with the resolved llm actually reaching a real call, analysis started
    failing with "'CompiledStateGraph' object has no attribute
    'with_structured_output'" - for every model, since LLMManager.agents
    has never stored anything with a `._llm` attribute."""

    def _service_with_fake_manager(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.application.services.contract_intelligence_service import ContractIntelligenceService

        fake_manager = MagicMock()
        # Simulates the real shape: agents holds compiled LangGraph
        # objects (no ._llm attribute at all - a plain MagicMock() would
        # accidentally auto-create one and mask this exact bug), raw_llms
        # holds the real chat model each was built from.
        compiled_graph_stub = object()
        fake_manager.agents = {"gpt-4o": compiled_graph_stub, "gemini-2.5-flash": object()}
        fake_manager.raw_llms = {
            "gpt-4o": _fake_llm("gpt-4o", None),
            "gemini-2.5-flash": _fake_llm("gemini-2.5-flash", None),
        }
        return ContractIntelligenceService(fake_manager), fake_manager

    def test_returns_the_real_llm_not_the_compiled_agent(self):
        service, fake_manager = self._service_with_fake_manager()
        resolved = service._get_llm_for_model("gpt-4o")
        self.assertIs(resolved, fake_manager.raw_llms["gpt-4o"])
        self.assertIsNot(resolved, fake_manager.agents["gpt-4o"])
        self.assertTrue(hasattr(resolved, "with_structured_output"))

    def test_default_model_also_resolves_to_a_real_llm(self):
        """The bug affected every model, including the default - not just
        non-default selections."""
        service, fake_manager = self._service_with_fake_manager()
        resolved = service._get_llm_for_model("gemini-2.5-flash")
        self.assertIs(resolved, fake_manager.raw_llms["gemini-2.5-flash"])

    def test_unknown_model_fails_instead_of_silently_substituting(self):
        service, fake_manager = self._service_with_fake_manager()
        with self.assertRaisesRegex(ValueError, "Selected analysis model"):
            service._get_llm_for_model("not-a-real-model")


if __name__ == "__main__":
    unittest.main()
