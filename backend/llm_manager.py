import os
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_mistralai import ChatMistralAI
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

from backend.contract_chat_agent import get_agent

from typing import Any
from backend.model_registry import MODEL_SPECS, model_spec


class LLMManager:
    agents = {}
    # Real, confirmed bug found live: contract_intelligence_service.py's
    # _get_llm_for_model tried `self.llm_manager.agents[model]._llm` to get
    # a raw, structured-output-capable chat model back out of `agents` -
    # but get_agent() below returns builder.compile(), a bare
    # CompiledStateGraph (the Contract Chat tool-calling agent), which
    # never had a `._llm` attribute at all. hasattr(...) was always False,
    # for every model, so _get_llm_for_model always fell through to
    # returning the compiled graph itself. This was invisible for a long
    # time because intelligence_tools.py's ClauseDetectorTool/
    # PolicyCheckerTool used to be constructed with no llm argument at all
    # (a separate, since-fixed bug) and silently fell back to their own
    # Gemini->OpenAI->Anthropic chain - once that threading was fixed, a
    # real analysis call hit CompiledStateGraph.with_structured_output(...)
    # and failed outright (confirmed live: "'CompiledStateGraph' object
    # has no attribute 'with_structured_output'"), for every model, not
    # just non-default ones. raw_llms keeps the actual chat model instance
    # each agent was built from, so real, structured-output-driven callers
    # (contract_intelligence_service.py) have a real one to resolve.
    raw_llms = {}
    specs = {}

    def __init__(self):
        # Provider availability is process configuration. Instance-owned maps
        # prevent one test/app instance from leaking stale configured models
        # into another after environment changes.
        self.agents = {}
        self.raw_llms = {}
        self.specs = {}
        self.init_agents()

    def init_agents(self):
        for spec in MODEL_SPECS:
            if not spec.configured or spec.deprecated:
                continue
            if (
                os.getenv("ENVIRONMENT", "development").lower() == "production"
                and not spec.production_allowed
            ):
                continue
            if spec.provider == "google":
                llm = ChatGoogleGenerativeAI(
                    model=spec.api_model,
                    temperature=0,
                    request_timeout=120,
                    max_retries=1,
                )
            elif spec.provider == "openai":
                llm = ChatOpenAI(model=spec.api_model, temperature=0, timeout=120, max_retries=1)
            elif spec.provider == "anthropic":
                llm = ChatAnthropic(model=spec.api_model, temperature=0, timeout=120, max_retries=1)
            elif spec.provider == "mistral":
                llm = ChatMistralAI(model=spec.api_model, temperature=0)
            else:
                continue
            self.raw_llms[spec.stable_id] = llm
            self.specs[spec.stable_id] = spec
            if {"chat", "tool_calling", "streaming"}.issubset(spec.capabilities):
                self.agents[spec.stable_id] = get_agent(llm)
        logger.info(f"Loaded {len(self.agents)} llms.")

    def get_model_by_name(self, name: str):
        try:
            return self.agents[name]
        except KeyError:
            raise ValueError(f"The model {name} wasn't initiated")

    def get_raw_model_by_name(self, name: str):
        """Return the provider chat model, never the compiled chat graph.

        Output validators and structured-output analysis operate at the raw
        provider boundary.  Contract Chat itself uses `get_model_by_name()`'s
        compiled `MessagesState` graph.  Keeping both lookups explicit prevents
        callers from invoking a graph with a provider-style string payload.
        """
        try:
            return self.raw_llms[name]
        except KeyError:
            raise ValueError(f"The raw model {name} wasn't initiated")

    def get_model_spec(self, name: str):
        # Registry validation is separate from client construction; callers
        # use this to persist requested/actual provider identity.
        if name not in self.specs:
            model_spec(name)  # raises the more precise unknown-model error
            raise ValueError(f"The model {name} wasn't initiated")
        return self.specs[name]
