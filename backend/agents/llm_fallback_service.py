"""
Real multi-provider LLM fallback for structured-output calls.

Motivated by a real production incident: Gemini was the only configured
provider in this deployment, and its only working model (gemini-2.5-flash)
hit a real, confirmed daily quota exhaustion (RESOURCE_EXHAUSTED, later a
"prepayment credits depleted" error even after billing was linked), taking
down PDF upload/analysis entirely for the rest of that day. There was no
other provider configured at all, so a single Google-side billing/quota
problem was a hard outage for the whole product.

This is resilience infrastructure, not a user-facing model-selector
feature: real callers never choose a provider. They call
invoke_with_fallback() and get whichever provider in the chain is
actually configured, not circuit-open, and successfully serves the
request right now - Gemini first, OpenAI second, Anthropic Claude third.

Why OpenAI before Anthropic: the extraction/policy-evaluation pipeline
depends entirely on LangChain's `with_structured_output(schema,
include_raw=True)` - the exact mechanism already used for Gemini.
OpenAI's function-calling is what that LangChain abstraction was
originally built around, making it the lowest-risk swap-in (most mature,
most-tested integration, least likely to behave subtly differently from
Gemini's structured-output shape). It's also a fully separate
infrastructure provider from Google - real protection against exactly
tonight's failure mode. Anthropic Claude's structured-output support is
also solid and becomes the second fallback tier if OpenAI is also down
or misconfigured.

A "qualifying failure" (falls through to the next provider) is narrowly
scoped to circuit-open, and errors that look like the provider itself
failed to serve this request - quota/rate-limit (429/RESOURCE_EXHAUSTED/
RateLimitError) or a timeout. Any other exception (a real bug: a bad
prompt, a schema mismatch, an auth/config error) is NOT treated as
fallback-worthy and re-raises immediately - silently rerouting every
exception to a different provider would mask real defects instead of
surfacing them, and would burn real money on providers that were never
going to succeed either.

Two ready-made chains are exposed, not one, because two real call-site
families have genuinely different timeout/isolation needs (see
circuit_breaker.py's RERANKER_CIRCUIT_BREAKER docstring for the original
rationale, extended here to the two new providers):
- invoke_with_fallback: extraction/policy evaluation - a 120s budget,
  using GEMINI_/OPENAI_/ANTHROPIC_CIRCUIT_BREAKER.
- invoke_with_fallback_reranker: search re-ranking - a short, strict
  budget on a synchronous user-facing request path, using the separate
  RERANKER_*_CIRCUIT_BREAKER instances so its tighter timeout can never
  spuriously trip the breakers guarding the load-bearing chain above.
"""

import os
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel

from backend.shared.reliability.circuit_breaker import (
    ANTHROPIC_CIRCUIT_BREAKER,
    CircuitBreakerOpenError,
    GEMINI_CIRCUIT_BREAKER,
    OPENAI_CIRCUIT_BREAKER,
    RERANKER_ANTHROPIC_CIRCUIT_BREAKER,
    RERANKER_CIRCUIT_BREAKER,
    RERANKER_OPENAI_CIRCUIT_BREAKER,
)
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

# Same discipline as llm_extraction_service.get_default_llm and
# reranker_service.get_reranker_llm: a real, enforced timeout (these SDKs'
# own defaults are effectively unbounded) and max_retries=1 (the SDK
# default silently retries a 429 for minutes before giving up - a quota
# wall cannot be waited out within one request's lifetime regardless, so
# fail fast and let provider-to-provider fallback handle it instead).
DEFAULT_TIMEOUT_SECONDS = 120.0

GEMINI_MODEL = "gemini-2.5-flash"
OPENAI_MODEL = "gpt-4o"
ANTHROPIC_MODEL = "claude-sonnet-5"


def _gemini_llm(timeout_seconds: float):
    if not os.getenv("GOOGLE_API_KEY"):
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL, temperature=0,
        request_timeout=timeout_seconds, max_retries=1,
    )


def _openai_llm(timeout_seconds: float):
    if not os.getenv("OPENAI_API_KEY"):
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=OPENAI_MODEL, temperature=0,
        timeout=timeout_seconds, max_retries=1,
    )


def _anthropic_llm(timeout_seconds: float):
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=ANTHROPIC_MODEL, temperature=0,
        timeout=timeout_seconds, max_retries=1,
    )


def _build_chain(gemini_breaker, openai_breaker, anthropic_breaker) -> List[Dict[str, Any]]:
    """Ordered: primary first, then fallbacks in priority order. See this
    module's docstring for the "why OpenAI before Anthropic" reasoning."""
    return [
        {"name": "gemini", "model": GEMINI_MODEL, "factory": _gemini_llm, "breaker": gemini_breaker},
        {"name": "openai", "model": OPENAI_MODEL, "factory": _openai_llm, "breaker": openai_breaker},
        {"name": "anthropic", "model": ANTHROPIC_MODEL, "factory": _anthropic_llm, "breaker": anthropic_breaker},
    ]


_EXTRACTION_CHAIN = _build_chain(GEMINI_CIRCUIT_BREAKER, OPENAI_CIRCUIT_BREAKER, ANTHROPIC_CIRCUIT_BREAKER)
_RERANKER_CHAIN = _build_chain(RERANKER_CIRCUIT_BREAKER, RERANKER_OPENAI_CIRCUIT_BREAKER, RERANKER_ANTHROPIC_CIRCUIT_BREAKER)

PRIMARY_PROVIDER = _EXTRACTION_CHAIN[0]["name"]


class AllProvidersExhaustedError(Exception):
    """Every provider in the chain was either unconfigured (no API key),
    circuit-open, or failed with a qualifying (quota/rate-limit/timeout)
    error for this specific call."""


def _is_quota_exhausted(e: Exception) -> bool:
    """True for google.api_core.exceptions.ResourceExhausted directly (kept
    for any client that does raise it), and also for the real exception
    this app's actual Gemini client raises: langchain_google_genai wraps a
    real 429 RESOURCE_EXHAUSTED into its own ChatGoogleGenerativeAIError
    (see langchain_google_genai.chat_models._handle_client_error), which is
    not a ResourceExhausted subclass - so `except ResourceExhausted` alone
    never actually matches it. Confirmed via live end-to-end testing
    against the real API (not a mock).

    Relocated here (was backend/agents/planning/execution_engine.py,
    removed with the rest of PlanExecutionEngine) - genuinely reusable,
    provider-error-classification logic with no dependency on that
    subsystem; research/benchmark/evaluate_extraction.py and this
    module's own quota-detection concerns both need it.
    """
    from google.api_core.exceptions import ResourceExhausted
    if isinstance(e, ResourceExhausted):
        return True
    message = str(e)
    return "RESOURCE_EXHAUSTED" in message or "429" in message


def _is_fallback_worthy(exc: Exception) -> bool:
    """True only for errors that look like the provider itself failed to
    serve this request (quota exhaustion, rate limiting, timeout) - never
    for an exception shape suggesting a real bug in our own code (a bad
    prompt, a schema mismatch, malformed input, bad credentials), which
    should surface immediately rather than being silently masked by a
    reroute to a provider that was never going to succeed either."""
    name = type(exc).__name__
    if name in (
        "RateLimitError", "APITimeoutError", "Timeout", "TimeoutError",
        "ReadTimeout", "InternalServerError", "APIConnectionError",
    ):
        return True
    text = str(exc).lower()
    markers = (
        "429", "resource_exhausted", "rate limit", "rate_limit", "quota",
        "timeout", "timed out", "prepayment credits", "503", "overloaded",
    )
    return any(m in text for m in markers)


def _invoke_chain(
    chain: List[Dict[str, Any]],
    schema: Type[BaseModel],
    prompt: str,
    *,
    operation: str,
    timeout_seconds: float,
) -> Tuple[Dict[str, Any], str, str]:
    last_error: Optional[Exception] = None
    attempted: List[str] = []

    for provider in chain:
        llm = provider["factory"](timeout_seconds)
        if llm is None:
            continue  # not configured - not an error, just unavailable

        attempted.append(provider["name"])
        try:
            with provider["breaker"].guard():
                structured_llm = llm.with_structured_output(schema, include_raw=True)
                raw_result = structured_llm.invoke(prompt)
            if provider["name"] != chain[0]["name"]:
                logger.warning(
                    f"LLM fallback: '{operation}' served by '{provider['name']}' "
                    f"(primary provider '{chain[0]['name']}' unavailable)"
                )
            return raw_result, provider["name"], provider["model"]
        except CircuitBreakerOpenError as e:
            logger.warning(f"LLM fallback: '{provider['name']}' circuit open for '{operation}' - {e}")
            last_error = e
            continue
        except Exception as e:
            if _is_fallback_worthy(e):
                logger.warning(f"LLM fallback: '{provider['name']}' failed for '{operation}' (falling back) - {e}")
                last_error = e
                continue
            raise

    raise AllProvidersExhaustedError(
        f"All providers exhausted for operation '{operation}' "
        f"(attempted: {attempted or 'none configured'}): {last_error}"
    )


def invoke_with_fallback(
    schema: Type[BaseModel],
    prompt: str,
    *,
    operation: str,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Extraction/policy-evaluation fallback chain (120s budget, the same
    GEMINI_CIRCUIT_BREAKER already guarding today's Gemini-only calls,
    plus OPENAI_CIRCUIT_BREAKER/ANTHROPIC_CIRCUIT_BREAKER).

    Tries each configured provider in order. Returns (raw_result,
    provider_name, model_name) for whichever provider actually served the
    request. raw_result is LangChain's own {"raw", "parsed",
    "parsing_error"} shape (include_raw=True) - identical to what a
    direct single-provider `structured_llm.invoke(prompt)` already
    returns, so callers built around that shape
    (llm_extraction_service._invoke, policy_evaluation_service.
    evaluate_clause) need no further changes beyond calling this instead
    of invoking their own bound LLM directly.

    Raises AllProvidersExhaustedError if every provider is unconfigured,
    circuit-open, or fails with a qualifying error. Raises the original
    exception unchanged (not wrapped) if a provider fails with a
    NON-qualifying error - see _is_fallback_worthy.
    """
    return _invoke_chain(_EXTRACTION_CHAIN, schema, prompt, operation=operation, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)


def invoke_with_fallback_reranker(
    schema: Type[BaseModel],
    prompt: str,
    *,
    operation: str,
    timeout_seconds: float,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Search re-ranking's own fallback chain - same provider order and
    qualifying-failure logic as invoke_with_fallback, but using
    RERANKER_CIRCUIT_BREAKER/RERANKER_OPENAI_CIRCUIT_BREAKER/
    RERANKER_ANTHROPIC_CIRCUIT_BREAKER (isolated from the extraction/
    policy-evaluation breakers above - see circuit_breaker.py) and a
    caller-supplied short timeout (reranker_service.RERANKER_TIMEOUT_
    SECONDS) instead of the 120s extraction/policy budget, since
    re-ranking runs on a synchronous, user-facing search request and must
    never be the reason a search takes noticeably longer than an
    unranked one.
    """
    return _invoke_chain(_RERANKER_CHAIN, schema, prompt, operation=operation, timeout_seconds=timeout_seconds)
