"""
Centralized LLM-based CUAD clause extraction.

Owns the CUAD taxonomy, the structured-output Pydantic schema, prompt
construction, the Gemini call, and response handling in one place.
LLMClauseExtractor (clause_extraction_agent.py), LLMCUADClassifier
(cuad_classifier_agent.py), and ClauseDetectorTool (intelligence_tools.py) are
thin wrappers that delegate here rather than each building their own
prompt/parsing logic - previously each had its own stubbed
_parse_llm_response() that discarded the LLM's response and returned [].
"""

import hashlib
import os
import re
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.shared.cache.redis_cache import cache
from backend.shared.config.phase3_config import Phase3Config
from backend.shared.monitoring.llm_usage_tracker import llm_usage_tracker
from backend.shared.monitoring.latency_tracker import track_latency
from backend.shared.utils.llm_concurrency import llm_call_semaphore
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

# Bumped whenever _build_prompt's wording changes, so a cached result from
# an old prompt version is never silently reused under a new one - the
# cache key is content + prompt version, not content alone.
#
# v2 (production-readiness audit finding #11): this had stayed "v1" since
# introduction despite _build_prompt's wording changing across several
# commits since then - decorative versioning that never actually busted
# the cache on those changes. Bumped now to reflect the prompt actually in
# use, and test_prompt_versioning.py enforces going forward: it hashes
# _build_prompt's real source against a table keyed by PROMPT_VERSION, so
# editing the prompt without bumping this constant (or bumping it without
# updating that table) fails CI.
PROMPT_VERSION = "v2"


class CUADClauseType(str, Enum):
    """
    The 41 CUAD clause categories (Atticus Project / CUAD paper), verified
    directly against the theatticusproject/cuad-qa dataset's own question
    categories (extracted from every row's "related to \"<category>\"" text).

    This is the single source of truth for the taxonomy in this codebase -
    other modules (e.g. cuad_classifier_agent.CUAD_CLAUSE_TYPES) derive from
    this rather than maintaining a parallel list.
    """
    DOCUMENT_NAME = "Document Name"
    PARTIES = "Parties"
    AGREEMENT_DATE = "Agreement Date"
    EFFECTIVE_DATE = "Effective Date"
    EXPIRATION_DATE = "Expiration Date"
    RENEWAL_TERM = "Renewal Term"
    NOTICE_PERIOD_TO_TERMINATE_RENEWAL = "Notice Period To Terminate Renewal"
    GOVERNING_LAW = "Governing Law"
    MOST_FAVORED_NATION = "Most Favored Nation"
    NON_COMPETE = "Non-Compete"
    EXCLUSIVITY = "Exclusivity"
    NO_SOLICIT_OF_CUSTOMERS = "No-Solicit Of Customers"
    COMPETITIVE_RESTRICTION_EXCEPTION = "Competitive Restriction Exception"
    NO_SOLICIT_OF_EMPLOYEES = "No-Solicit Of Employees"
    NON_DISPARAGEMENT = "Non-Disparagement"
    TERMINATION_FOR_CONVENIENCE = "Termination For Convenience"
    ROFR_ROFO_ROFN = "Rofr/Rofo/Rofn"
    CHANGE_OF_CONTROL = "Change Of Control"
    ANTI_ASSIGNMENT = "Anti-Assignment"
    REVENUE_PROFIT_SHARING = "Revenue/Profit Sharing"
    PRICE_RESTRICTIONS = "Price Restrictions"
    MINIMUM_COMMITMENT = "Minimum Commitment"
    VOLUME_RESTRICTION = "Volume Restriction"
    IP_OWNERSHIP_ASSIGNMENT = "Ip Ownership Assignment"
    JOINT_IP_OWNERSHIP = "Joint Ip Ownership"
    LICENSE_GRANT = "License Grant"
    NON_TRANSFERABLE_LICENSE = "Non-Transferable License"
    AFFILIATE_LICENSE_LICENSOR = "Affiliate License-Licensor"
    AFFILIATE_LICENSE_LICENSEE = "Affiliate License-Licensee"
    UNLIMITED_ALL_YOU_CAN_EAT_LICENSE = "Unlimited/All-You-Can-Eat-License"
    IRREVOCABLE_OR_PERPETUAL_LICENSE = "Irrevocable Or Perpetual License"
    SOURCE_CODE_ESCROW = "Source Code Escrow"
    POST_TERMINATION_SERVICES = "Post-Termination Services"
    AUDIT_RIGHTS = "Audit Rights"
    UNCAPPED_LIABILITY = "Uncapped Liability"
    CAP_ON_LIABILITY = "Cap On Liability"
    LIQUIDATED_DAMAGES = "Liquidated Damages"
    WARRANTY_DURATION = "Warranty Duration"
    INSURANCE = "Insurance"
    COVENANT_NOT_TO_SUE = "Covenant Not To Sue"
    THIRD_PARTY_BENEFICIARY = "Third Party Beneficiary"


class _LLMExtractedClause(BaseModel):
    """
    Schema bound directly to the LLM via structured output. Offsets are
    deliberately NOT part of this schema: general-purpose LLMs cannot
    reliably count characters over multi-thousand-character documents, so
    asking for start/end offsets directly produces hallucinated numbers.
    Offsets are instead computed deterministically by searching for
    extracted_text as a substring of the source text after the LLM call
    returns (see LLMExtractionService._find_span).
    """
    clause_type: CUADClauseType
    extracted_text: str = Field(
        description="Verbatim text of the clause exactly as it appears in the source contract text"
    )
    confidence: float = Field(ge=0.0, le=1.0)


class _LLMExtractionResponse(BaseModel):
    clauses: List[_LLMExtractedClause] = Field(default_factory=list)


class ExtractedClause(BaseModel):
    """Public result type returned by LLMExtractionService.extract_clauses()."""
    clause_type: CUADClauseType
    extracted_text: str
    start_offset: int
    end_offset: int
    confidence: float


DEFAULT_MODEL = "gemini-2.5-flash"


def get_default_llm(model: str = DEFAULT_MODEL):
    """
    Lazily construct the default extraction LLM. Returns None (rather than
    raising) if no GOOGLE_API_KEY is configured, so callers that construct
    tools eagerly but only call them conditionally (e.g. execution_engine.py's
    tool dict, built once at engine construction) don't crash just because no
    key is present yet.

    `model` defaults to the production model (gemini-2.5-flash) and exists as
    an override mainly so research/benchmark/evaluate_extraction.py can point
    at a different model (e.g. when a free-tier daily quota is exhausted)
    without changing what the deployed tools use.
    """
    if not os.getenv("GOOGLE_API_KEY"):
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI
    # request_timeout defaults to None (no timeout at all) in this client,
    # which lets a single stalled connection hang indefinitely - observed in
    # practice after this process's environment was suspended/resumed mid-
    # request. 120s is generous for a single-pass extraction call but still
    # bounded.
    #
    # max_retries defaults to 6 in this client (langchain_google_genai maps
    # it directly to google.genai.types.HttpRetryOptions(attempts=6)), which
    # retries a 429 RESOURCE_EXHAUSTED with exponential backoff (up to 60s
    # between attempts) before finally giving up - observed in practice
    # taking several minutes across a handful of clause/policy evaluations,
    # each independently retrying up to 6 times. A per-day quota wall cannot
    # be waited out within one request's lifetime regardless (see
    # execution_engine.py's StepExecutor, which already fails fast rather
    # than retrying on a quota error at its own layer) - attempts=1 ("1 or 0
    # means no retries" per HttpRetryOptions) means a real quota/rate-limit
    # error surfaces immediately instead of after minutes of internal SDK
    # backoff.
    return ChatGoogleGenerativeAI(model=model, temperature=0, request_timeout=120, max_retries=1)


class LLMExtractionService:
    """
    Owns the prompt, the structured-output call, and response handling for
    LLM-based CUAD clause extraction. Single-pass: one call per invocation
    asks Gemini to identify every applicable clause type in the given text.
    """

    def __init__(self, llm=None):
        self.llm = llm
        self._model_name = getattr(llm, "model", None) or DEFAULT_MODEL
        self._structured_llm = (
            llm.with_structured_output(_LLMExtractionResponse, include_raw=True) if llm else None
        )

    # Audit finding #13: this call (with evaluate_clause in
    # policy_evaluation_service.py) dominates real per-contract cost/
    # latency - previously only the secondary CUAD-mitigation tools
    # (optimized_cuad_tools.py) had @track_performance coverage, so
    # p50/p95 for the actual primary path was unanswerable. Uses
    # @track_latency (Redis-backed, shared/monitoring/latency_tracker.py)
    # rather than the in-process @track_performance those tools use: this
    # call runs in the Celery `worker` container while GET /metrics is
    # served by `backend`, the same cross-process gap findings #1/#12
    # already closed for cost/token and hallucination-rate tracking - so
    # latency gets the identical treatment here rather than being left
    # queryable from only one of the two processes that can run it.
    @track_latency("clause_extraction")
    def extract_clauses(
        self,
        text: str,
        candidate_types: Optional[List[CUADClauseType]] = None,
        raise_on_error: bool = False,
    ) -> List[ExtractedClause]:
        """
        Extract all applicable CUAD clauses from `text` in a single LLM call.

        candidate_types optionally restricts which categories the model is
        asked about - used by LLMCUADClassifier to narrow classification of
        an already-extracted clause to a keyword-relevant subset instead of
        all 41 types.

        raise_on_error defaults to False (production behavior: degrade to []
        rather than crash a caller on a transient LLM/network failure).
        research/benchmark/evaluate_extraction.py passes True so it can tell
        a genuine "no clauses found" apart from a quota/network failure and
        stop a long batch run cleanly instead of burning through the rest of
        it on calls that are guaranteed to keep failing.

        Re-analyzing the same contract text previously re-billed the LLM
        every time (docs/ENTERPRISE_READINESS.md §9) - results are now
        cached on a hash of (prompt version, model, candidate_types, text),
        so an identical request is free and instant on a cache hit.
        """
        if not self._structured_llm or not text or not text.strip():
            return []

        cache_key = self._cache_key(text, candidate_types)
        if Phase3Config.CACHE_ENABLED:
            cached = cache.get(cache_key)
            if cached is not None:
                llm_usage_tracker.record_call("clause_extraction", self._model_name, cache_hit=True)
                return [ExtractedClause(**c) for c in cached]

        prompt = self._build_prompt(text, candidate_types)

        try:
            with llm_call_semaphore:
                raw_result = self._structured_llm.invoke(prompt)
        except Exception as e:
            logger.error(f"LLM clause extraction failed: {e}")
            if raise_on_error:
                raise
            return []

        response = raw_result.get("parsed")
        if response is None:
            logger.error(
                f"LLM clause extraction returned no parsed result "
                f"(parsing_error={raw_result.get('parsing_error')})"
            )
            if raise_on_error:
                raise raw_result.get("parsing_error") or ValueError("LLM structured output parsing failed")
            return []

        usage_metadata = getattr(raw_result.get("raw"), "usage_metadata", None)
        llm_usage_tracker.record_call(
            "clause_extraction", self._model_name, cache_hit=False, usage_metadata=usage_metadata
        )

        result = [self._resolve_offsets(clause, text) for clause in response.clauses]

        if Phase3Config.CACHE_ENABLED:
            cache.set(
                cache_key,
                [c.model_dump() for c in result],
                ttl=Phase3Config.get_cache_ttl("clause_extraction"),
            )

        return result

    def _cache_key(self, text: str, candidate_types: Optional[List[CUADClauseType]]) -> str:
        types_part = ",".join(sorted(t.value for t in (candidate_types or [])))
        raw = f"clause_extraction:{PROMPT_VERSION}:{self._model_name}:{types_part}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _build_prompt(self, text: str, candidate_types: Optional[List[CUADClauseType]]) -> str:
        types = candidate_types or list(CUADClauseType)
        type_list = "\n".join(f"- {t.value}" for t in types)
        return f"""You are a contract analysis assistant. Identify every clause in the
contract text below that matches one of the following CUAD clause categories:

{type_list}

For each match, extract the clause VERBATIM - do not paraphrase, summarize,
reword, or alter whitespace/punctuation - so that it can be located as an
exact substring of the source text below. Include a confidence score between
0.0 and 1.0. Only include matches you are reasonably confident about. If a
category has no matching clause in this text, omit it entirely.

Contract text:
{text}"""

    def _resolve_offsets(self, clause: _LLMExtractedClause, source_text: str) -> ExtractedClause:
        start, end = self._find_span(clause.extracted_text, source_text)
        return ExtractedClause(
            clause_type=clause.clause_type,
            extracted_text=clause.extracted_text,
            start_offset=start,
            end_offset=end,
            confidence=clause.confidence,
        )

    @staticmethod
    def _find_span(extracted_text: str, source_text: str) -> Tuple[int, int]:
        """
        Locate extracted_text as a substring of source_text. Falls back to a
        whitespace-insensitive match, since LLMs occasionally normalize
        whitespace even when asked not to. Returns (-1, -1) if no reasonable
        match can be found (the clause is still returned to the caller with
        those sentinel offsets rather than being dropped).
        """
        if not extracted_text:
            return -1, -1

        idx = source_text.find(extracted_text)
        if idx != -1:
            return idx, idx + len(extracted_text)

        words = extracted_text.split()
        if not words:
            return -1, -1
        pattern = r"\s+".join(re.escape(w) for w in words)
        match = re.search(pattern, source_text)
        if match:
            return match.start(), match.end()

        return -1, -1
