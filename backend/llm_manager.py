import os
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_mistralai import ChatMistralAI
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

from backend.contract_chat_agent import get_agent

from typing import Any


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

    def __init__(self):
        self.init_agents()

    def init_agents(self):
        if os.getenv("OPENAI_API_KEY"):
            llm = ChatOpenAI(model="gpt-4o", temperature=0)
            self.raw_llms["gpt-4o"] = llm
            self.agents["gpt-4o"] = get_agent(llm)

        if os.getenv("GOOGLE_API_KEY"):
            # request_timeout/max_retries: without these, this client relies
            # entirely on the SDK's own defaults (no timeout at all, 6
            # retries with growing exponential backoff on a 429) - see
            # backend/agents/llm_extraction_service.py's get_default_llm for
            # the same fix and its full rationale (found via live testing
            # and an overnight benchmark run that hung for ~3 hours on a
            # stalled connection).
            gemini_pro = ChatGoogleGenerativeAI(
                model="gemini-2.5-pro",
                temperature=0,
                request_timeout=120,
                max_retries=1,
            )
            self.raw_llms["gemini-2.5-pro"] = gemini_pro
            self.agents["gemini-2.5-pro"] = get_agent(gemini_pro)

            gemini_flash = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                temperature=0,
                request_timeout=120,
                max_retries=1,
            )
            self.raw_llms["gemini-2.5-flash"] = gemini_flash
            self.agents["gemini-2.5-flash"] = get_agent(gemini_flash)


        if os.getenv("ANTHROPIC_API_KEY"):
            claude = ChatAnthropic(
                model="claude-3-5-sonnet-latest",
                temperature=0,
            )
            self.raw_llms["sonnet-3.5"] = claude
            self.agents["sonnet-3.5"] = get_agent(claude)

        if os.getenv("MISTRAL_API_KEY"):
            mistral = ChatMistralAI(
                model="mistral-large-latest",
            )
            self.raw_llms["mistral-large"] = mistral
            self.agents["mistral-large"] = get_agent(mistral)
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
