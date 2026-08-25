import os
from collections.abc import Mapping
from langchain_google_genai import ChatGoogleGenerativeAI
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

from backend.contract_chat_agent import get_agent

from typing import Any, Callable
from backend.model_registry import MODEL_SPECS, DEFAULT_MODEL, model_spec


class _LazyModelMap(Mapping):
    """Read-only view over every CONFIGURED model's stable_id, real values
    built on first access rather than eagerly.

    Real, measured root cause (found live: 3 consecutive production
    deployments needed a manual worker-restart to recover from a memory-
    pressure cold-start): constructing a provider's LangChain chat-model
    client is not free, and *importing* `langchain_openai` specifically
    costs more than constructing the client does (measured locally: +22MB
    import vs +15MB construction). Building all 3 configured providers
    (Gemini/OpenAI/Anthropic) unconditionally at process startup, in both
    `backend` and `worker` simultaneously, was real, avoidable memory
    pressure - OpenAI/Anthropic are fallback-only (see
    llm_fallback_service.py: Gemini is PRIMARY_PROVIDER, the others only
    get invoked if it fails), so most process lifetimes never need them
    at all.

    Every configured stable_id is a real key from the moment
    LLMManager.init_agents() registers it, so `in`, `len()`, `keys()`,
    iteration all correctly reflect true availability immediately -
    nothing observing this map from outside can tell the difference from
    an eagerly-built dict except that the expensive part (and, for
    OpenAI/Anthropic/Mistral, the SDK import itself - see
    LLMManager._construct_raw) is deferred to the first real
    `__getitem__`/`.get()` on that specific key.
    """

    def __init__(self, build_fn: Callable[[str], Any]):
        self._build_fn = build_fn
        self._known: dict[str, bool] = {}
        self._built: dict[str, Any] = {}

    def register(self, stable_id: str) -> None:
        self._known[stable_id] = True

    def __getitem__(self, key: str) -> Any:
        if key not in self._known:
            raise KeyError(key)
        if key not in self._built:
            self._built[key] = self._build_fn(key)
        return self._built[key]

    def __iter__(self):
        return iter(self._known)

    def __len__(self) -> int:
        return len(self._known)

    def built_count(self) -> int:
        """How many of the registered keys have actually been built so
        far - for startup logging, not correctness (every registered key
        is already usable regardless of this count)."""
        return len(self._built)


class LLMManager:
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

    def __init__(self):
        # Provider availability is process configuration. Instance-owned maps
        # prevent one test/app instance from leaking stale configured models
        # into another after environment changes.
        self.specs: dict[str, Any] = {}
        self._agent_capable: set[str] = set()
        self.raw_llms = _LazyModelMap(self._construct_raw)
        self.agents = _LazyModelMap(self._construct_agent)
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
            self.specs[spec.stable_id] = spec
            self.raw_llms.register(spec.stable_id)
            if {"chat", "tool_calling", "streaming"}.issubset(spec.capabilities):
                self._agent_capable.add(spec.stable_id)
                self.agents.register(spec.stable_id)

        # Only the default model is built eagerly - every real request
        # needs it immediately, so deferring it would just move this
        # deployment's cold-start cost from process-init time to
        # first-request time rather than actually removing it. Every
        # other configured model here is fallback-only (see
        # llm_fallback_service.py) and gets built lazily, on its own
        # first real use, via _LazyModelMap above.
        if DEFAULT_MODEL in self.specs:
            self.raw_llms[DEFAULT_MODEL]
            if DEFAULT_MODEL in self._agent_capable:
                self.agents[DEFAULT_MODEL]

        logger.info(
            f"{len(self.specs)} models configured "
            f"({self.raw_llms.built_count()} built eagerly at startup, "
            "remainder lazy on first use)."
        )

    def _construct_raw(self, stable_id: str):
        spec = self.specs[stable_id]
        if spec.provider == "google":
            # Always imported at module level (see top of file) - every
            # process needs this immediately for DEFAULT_MODEL, so there's
            # no cold-start cost left to defer by making this import lazy
            # too.
            return ChatGoogleGenerativeAI(
                model=spec.api_model,
                temperature=0,
                request_timeout=120,
                max_retries=1,
            )
        elif spec.provider == "openai":
            # Deliberately a local import, not module-level: measured live
            # that importing langchain_openai alone costs more than
            # constructing the client does (+22MB vs +15MB locally) - real
            # root cause of a memory-pressure cold-start that hit 3
            # consecutive production deployments. OpenAI is fallback-only
            # (llm_fallback_service.py), so most process lifetimes never
            # take this branch at all.
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=spec.api_model, temperature=0, timeout=120, max_retries=1)
        elif spec.provider == "anthropic":
            # Real, confirmed bug found live: Anthropic deprecated the
            # temperature/top_p/top_k sampling parameters entirely for
            # Claude Opus 4.7+ and Claude Sonnet 5 (shipped 2026-06-30)
            # - passing temperature=0 here made every real API call to
            # claude-sonnet-5 fail with a real 400, "`temperature` is
            # deprecated for this model" (confirmed via direct API
            # call, both plain text and vision, independent of this
            # engagement's other work). The deprecation triggers on the
            # parameter's *presence* in the request, not its value, so
            # temperature=1 (a "no-op" value) still 400s - Anthropic's
            # own migration guidance is to omit the parameter entirely
            # and let the model use its default sampling behavior, not
            # substitute a specific value. See platform.claude.com's
            # migration guide and "What's new in Claude Sonnet 5."
            # Every other provider still gets a real temperature=0
            # override; this is Anthropic-only and applies regardless
            # of which specific Claude model is configured, matching
            # Anthropic's own forward direction (already applied to two
            # consecutive model families).
            #
            # Local import for the same cold-start reason as OpenAI above -
            # Anthropic is fallback-only too.
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=spec.api_model, timeout=120, max_retries=1)
        elif spec.provider == "mistral":
            # Local import - Mistral is never configured in production
            # today (no MISTRAL_API_KEY), so this branch, and the SDK
            # import it needs, currently never executes there at all.
            from langchain_mistralai import ChatMistralAI
            return ChatMistralAI(model=spec.api_model, temperature=0)
        raise ValueError(f"Unsupported provider: {spec.provider}")

    def _construct_agent(self, stable_id: str):
        return get_agent(self.raw_llms[stable_id])

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
