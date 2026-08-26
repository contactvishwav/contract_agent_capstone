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

import asyncio
import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.agents.llm_fallback_service import AllProvidersExhaustedError, PRIMARY_PROVIDER, invoke_with_fallback
from backend.shared.cache.redis_cache import cache
from backend.shared.config.phase3_config import Phase3Config
from backend.shared.monitoring.llm_usage_tracker import llm_usage_tracker
from backend.shared.monitoring.latency_tracker import track_latency
from backend.shared.reliability.circuit_breaker import GEMINI_CIRCUIT_BREAKER, CircuitBreakerOpenError
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
#
# v3 (weak-category accuracy pass, docs/EVALUATION.md): _build_prompt now
# appends a short per-category hint for the categories that were scoring
# below 0.30 F1 due to genuine recall/precision gaps (see _CATEGORY_HINTS),
# and extract_clauses gained a conditional fallback pass (see
# FALLBACK_CATEGORIES / _run_fallback_pass) for the categories the model
# was essentially never attempting. Bumped so cached v2 results (extracted
# without either change) are never served under the new behavior.
PROMPT_VERSION = "v3"


class CUADClauseType(str, Enum):
    """
    The 41 CUAD clause categories (Atticus Project / CUAD paper), verified
    directly against the theatticusproject/cuad-qa dataset's own question
    categories (extracted from every row's "related to \"<category>\"" text),
    plus 2 supplemental categories real production use found missing from
    that academic taxonomy (see docs/CUAD_LIMITATIONS_AND_MITIGATION.md -
    CUAD was built for M&A due-diligence deal-term spotting, not general
    commercial contract review, and never covered these at all):

    - Indemnification: real, confirmed gap - a full one-way indemnification
      clause (including the counterparty's own negligence) went completely
      unextracted and unflagged on a real production contract, despite an
      existing default policy rule (default_indemnification_scope) and a
      tenant-uploaded playbook rule that were both fully ready to catch it.
    - Payment Terms: same real gap, same contract - Net-90 and satisfaction-
      contingent payment language, explicitly called out as unacceptable by
      the tenant's own uploaded policy playbook, was never extracted either.

    These 2 are deliberately excluded from research/benchmark/evaluate_
    extraction.py's RISK_CATEGORY_TYPES - the real CUAD dataset's ground
    truth has no labels for them, so scoring them there would register
    every true extraction as a false positive and corrupt the tracked
    benchmark numbers in docs/EVALUATION.md.

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

    # Supplemental categories, not part of the original 41 CUAD categories -
    # see this class's docstring for why they were added and how the
    # benchmark script keeps them from corrupting its CUAD-only scoring.
    INDEMNIFICATION = "Indemnification"
    PAYMENT_TERMS = "Payment Terms"


# Root-caused against the 497-contract flash-lite benchmark
# (docs/EVALUATION.md, weak-category accuracy pass): the 20 risk-relevant
# categories scoring below 0.30 F1 split into two groups, driven by real
# per-contract failure inspection, not guesswork.
#
# Group A/C below: the model DOES engage with these categories (real TPs
# exist) but under-recalls, or in two cases over-triggers on the wrong
# clause shape. A one-line hint appended next to the category name in the
# main single-pass prompt is the proportionate fix - these are not
# candidates for the heavier fallback pass because the model isn't
# ignoring them, it just needs sharper guidance than the bare category
# name provides.
#
# Group A (precision fixes - the model finds *a* clause but the wrong one):
#   - Third Party Beneficiary: 103 of 497 contracts got a false positive,
#     almost all boilerplate "no third party beneficiaries" DISCLAIMER
#     clauses being matched as if they granted rights.
#   - Change Of Control: 18 of 96 false negatives were the model quoting
#     the bare definition of "Change of Control" instead of the operative
#     consequence clause CUAD's gold span actually points to.
# Group C (recall gaps - the model already finds some real examples):
#   Non-Disparagement, Most Favored Nation, Source Code Escrow,
#   Rofr/Rofo/Rofn, Ip Ownership Assignment, Revenue/Profit Sharing,
#   Minimum Commitment, Exclusivity, Uncapped Liability, Price Restrictions.
#
# Deliberately NOT applied to the 8 FALLBACK_CATEGORIES below, or to the
# categories that already score well (Document Name/Parties/dates etc.) -
# padding every category with a hint would dilute the already-dense
# 41-category prompt without a demonstrated need for the categories that
# work today.
_CATEGORY_HINTS: Dict["CUADClauseType", str] = {
    CUADClauseType.THIRD_PARTY_BENEFICIARY: (
        "only a clause that GRANTS enforcement rights to a non-signatory "
        "third party - do NOT match boilerplate stating there are NO "
        "third-party beneficiaries, that language is the opposite of this category"
    ),
    CUADClauseType.CHANGE_OF_CONTROL: (
        "the operative clause describing what happens upon a change of "
        "control (e.g. consent required, assignment restricted, termination "
        "triggered) - not a bare definition of the term \"Change of Control\""
    ),
    CUADClauseType.NON_DISPARAGEMENT: (
        "a promise not to make negative or disparaging public statements about the other party"
    ),
    CUADClauseType.MOST_FAVORED_NATION: (
        "a promise to extend the best price or terms given to any other customer"
    ),
    CUADClauseType.SOURCE_CODE_ESCROW: (
        "an obligation to deposit source code with a third-party escrow agent"
    ),
    CUADClauseType.ROFR_ROFO_ROFN: (
        "a right of first refusal, first offer, or first negotiation before "
        "a party may deal with a third party"
    ),
    CUADClauseType.IP_OWNERSHIP_ASSIGNMENT: (
        "a clause assigning ownership of newly-created intellectual property to one of the parties"
    ),
    CUADClauseType.REVENUE_PROFIT_SHARING: (
        "an obligation to share a percentage of revenue or profit with the other party"
    ),
    CUADClauseType.MINIMUM_COMMITMENT: (
        "a minimum purchase, volume, usage, or spend the counterparty must commit to"
    ),
    CUADClauseType.EXCLUSIVITY: (
        "an obligation to deal exclusively with the other party in some market, territory, or product line"
    ),
    CUADClauseType.UNCAPPED_LIABILITY: (
        "a carve-out stating some type of liability is NOT subject to the contract's liability cap"
    ),
    CUADClauseType.PRICE_RESTRICTIONS: (
        "a restriction on the price a party may charge, e.g. a resale price or MSRP floor/ceiling"
    ),
}

# The 8 categories where the 497-contract benchmark showed the model
# essentially never attempting the category at all (0-1 true positives,
# near-zero false positives too - not wrong guesses, no guesses). A prompt
# hint alone is unlikely to fix "never engages with this category" inside
# an already-dense 41-category list, so these instead get a smaller,
# dedicated follow-up LLM call (see _run_fallback_pass) - fired only when
# the primary pass found none of them, mirroring the deterministic-table
# vs. LLM-reasoned split already used in PolicyEvaluationService /
# evaluate_deterministic, adapted here as "broad single pass" vs.
# "targeted narrow pass" rather than "deterministic" vs. "LLM".
FALLBACK_CATEGORIES: List["CUADClauseType"] = [
    CUADClauseType.VOLUME_RESTRICTION,
    CUADClauseType.COMPETITIVE_RESTRICTION_EXCEPTION,
    CUADClauseType.UNLIMITED_ALL_YOU_CAN_EAT_LICENSE,
    CUADClauseType.JOINT_IP_OWNERSHIP,
    CUADClauseType.AFFILIATE_LICENSE_LICENSEE,
    CUADClauseType.AFFILIATE_LICENSE_LICENSOR,
    CUADClauseType.COVENANT_NOT_TO_SUE,
    CUADClauseType.POST_TERMINATION_SERVICES,
]

# Richer per-category guidance for the fallback pass - a hint plus a short
# illustrative example, since this call has far fewer competing categories
# to describe (8 instead of 41) and exists specifically because a bare
# category name plus one-line hint was judged insufficient for categories
# the model wasn't attempting at all.
_FALLBACK_CATEGORY_GUIDANCE: Dict["CUADClauseType", str] = {
    CUADClauseType.VOLUME_RESTRICTION: (
        "a cap on the QUANTITY a party may buy, sell, or use. "
        'Example: "Distributor shall not resell more than 10,000 units of the Product per calendar quarter."'
    ),
    CUADClauseType.COMPETITIVE_RESTRICTION_EXCEPTION: (
        "a carve-out EXCUSING a party from an otherwise-applicable non-compete or exclusivity obligation. "
        'Example: "Notwithstanding the foregoing, Company may continue to operate its existing Widget business."'
    ),
    CUADClauseType.UNLIMITED_ALL_YOU_CAN_EAT_LICENSE: (
        "a license granting unlimited use, copies, or users with no quantity cap. "
        'Example: "Licensee may install and use the Software on an unlimited number of devices."'
    ),
    CUADClauseType.JOINT_IP_OWNERSHIP: (
        "a clause stating intellectual property will be OWNED JOINTLY by both parties, not assigned to one. "
        'Example: "Any Improvements developed jointly shall be jointly owned by both parties."'
    ),
    CUADClauseType.AFFILIATE_LICENSE_LICENSEE: (
        "a clause extending the LICENSE GRANT to the licensee's affiliates/subsidiaries, not just the signing party. "
        'Example: "The license granted herein extends to Licensee\'s Affiliates."'
    ),
    CUADClauseType.AFFILIATE_LICENSE_LICENSOR: (
        "a clause under which the LICENSOR's affiliates (not just the signing licensor) also grant rights or are bound. "
        'Example: "Licensor and its Affiliates hereby grant to Licensee a license under any patents they own."'
    ),
    CUADClauseType.COVENANT_NOT_TO_SUE: (
        "a promise not to bring a legal claim or lawsuit against the other party, distinct from a liability cap or release. "
        'Example: "Company covenants not to sue Customer for infringement of the Licensed Patents."'
    ),
    CUADClauseType.POST_TERMINATION_SERVICES: (
        "an obligation to continue providing services, support, or transition assistance for a period AFTER the agreement ends. "
        'Example: "Following termination, Vendor shall provide transition assistance for up to 90 days."'
    ),
}

# PILOT (not wired into any production call path yet - see
# extract_clauses_grouped and research/benchmark/pilot_grouped_extraction.py):
# a competing hypothesis for the same attention-dilution problem
# _CATEGORY_HINTS/FALLBACK_CATEGORIES were built to address. Instead of a
# hint or a conditional second call for a hand-picked 8 categories, this
# splits ALL categories (the 41 CUAD + 2 supplemental) into 7 always-run,
# fully-isolated groups (one prompt/call per group, run concurrently) - the
# question this groups is built to test is whether isolation itself (never
# competing with every other category for the model's attention) closes
# the gap more generally than the targeted patches above, not just for the
# 8 FALLBACK_CATEGORIES.
#
# Grouped by real-world contract-review taxonomy (paralleling how a legal
# reviewer already mentally buckets clause types, and the categories the
# original enterprise-readiness audit's risk-category list itself organized
# around), not by benchmark performance - every category has a natural home
# regardless of how it scored under single-pass extraction, so the grouping
# doesn't just re-encode "which categories were already weak."
#
# liability_indemnity's name anticipated Indemnification belonging here
# well before the category itself existed - the real gap this group's name
# already named (see CUADClauseType's docstring) is now closed.
CATEGORY_GROUPS: Dict[str, List["CUADClauseType"]] = {
    "metadata": [
        CUADClauseType.DOCUMENT_NAME, CUADClauseType.PARTIES,
        CUADClauseType.AGREEMENT_DATE, CUADClauseType.EFFECTIVE_DATE,
        CUADClauseType.EXPIRATION_DATE,
    ],
    "liability_indemnity": [
        CUADClauseType.CAP_ON_LIABILITY, CUADClauseType.UNCAPPED_LIABILITY,
        CUADClauseType.LIQUIDATED_DAMAGES, CUADClauseType.WARRANTY_DURATION,
        CUADClauseType.INSURANCE, CUADClauseType.AUDIT_RIGHTS,
        CUADClauseType.COVENANT_NOT_TO_SUE, CUADClauseType.INDEMNIFICATION,
    ],
    "termination_continuity": [
        CUADClauseType.RENEWAL_TERM, CUADClauseType.NOTICE_PERIOD_TO_TERMINATE_RENEWAL,
        CUADClauseType.TERMINATION_FOR_CONVENIENCE, CUADClauseType.CHANGE_OF_CONTROL,
        CUADClauseType.POST_TERMINATION_SERVICES,
    ],
    "restrictive_covenants": [
        CUADClauseType.NON_COMPETE, CUADClauseType.EXCLUSIVITY,
        CUADClauseType.NO_SOLICIT_OF_CUSTOMERS, CUADClauseType.COMPETITIVE_RESTRICTION_EXCEPTION,
        CUADClauseType.NO_SOLICIT_OF_EMPLOYEES, CUADClauseType.NON_DISPARAGEMENT,
        CUADClauseType.ROFR_ROFO_ROFN,
    ],
    "ip": [
        CUADClauseType.IP_OWNERSHIP_ASSIGNMENT, CUADClauseType.JOINT_IP_OWNERSHIP,
        CUADClauseType.LICENSE_GRANT, CUADClauseType.NON_TRANSFERABLE_LICENSE,
        CUADClauseType.AFFILIATE_LICENSE_LICENSOR, CUADClauseType.AFFILIATE_LICENSE_LICENSEE,
        CUADClauseType.UNLIMITED_ALL_YOU_CAN_EAT_LICENSE, CUADClauseType.IRREVOCABLE_OR_PERPETUAL_LICENSE,
        CUADClauseType.SOURCE_CODE_ESCROW,
    ],
    "commercial_terms": [
        CUADClauseType.MOST_FAVORED_NATION, CUADClauseType.REVENUE_PROFIT_SHARING,
        CUADClauseType.PRICE_RESTRICTIONS, CUADClauseType.MINIMUM_COMMITMENT,
        CUADClauseType.VOLUME_RESTRICTION, CUADClauseType.PAYMENT_TERMS,
    ],
    "governance": [
        CUADClauseType.GOVERNING_LAW, CUADClauseType.ANTI_ASSIGNMENT,
        CUADClauseType.THIRD_PARTY_BENEFICIARY,
    ],
}

# Every category (the 41 CUAD + 2 supplemental) must appear in exactly one
# group - enforced at import time (not just by a test) since a silently-
# dropped or double-counted category would corrupt any comparison against
# single-pass extraction (which always considers every category).
_grouped_types = [t for types in CATEGORY_GROUPS.values() for t in types]
assert len(_grouped_types) == len(CUADClauseType), (
    f"CATEGORY_GROUPS covers {len(_grouped_types)} category slots, expected {len(CUADClauseType)}"
)
assert len(set(_grouped_types)) == len(CUADClauseType), "CATEGORY_GROUPS has a duplicate category"
assert set(_grouped_types) == set(CUADClauseType), "CATEGORY_GROUPS is missing a category"
del _grouped_types


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
    tools eagerly but only call them conditionally don't crash just because
    no key is present yet.

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
    # llm_fallback_service.py's _is_quota_exhausted, which already fails
    # fast rather than retrying on a quota error at its own layer) - attempts=1 ("1 or 0
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

    def __init__(self, llm=None, use_fallback: bool = False):
        """
        use_fallback: real multi-provider fallback (backend/agents/
        llm_fallback_service.py) - only takes effect when `llm` is None
        (a caller that explicitly passed its own llm has made a deliberate
        single-provider choice, e.g. research/benchmark scripts pinning a
        specific model for reproducible numbers, or a test double - that
        choice is always respected as-is, never silently overridden).
        Real production callers (intelligence_tools.py's ClauseDetectorTool)
        pass use_fallback=True with no llm instead of eagerly resolving
        get_default_llm() themselves, so a missing/exhausted Gemini key no
        longer means clause extraction is unconditionally unavailable.
        """
        self.llm = llm
        self.use_fallback = use_fallback and llm is None
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
        enable_fallback: bool = True,
    ) -> List[ExtractedClause]:
        """
        Extract all applicable CUAD clauses from `text` in a single LLM call,
        plus (by default) a second, smaller conditional call for the small
        set of categories the primary pass tends not to attempt at all.

        candidate_types optionally restricts which categories the model is
        asked about - used by LLMCUADClassifier to narrow classification of
        an already-extracted clause to a keyword-relevant subset instead of
        all 41 types. The fallback pass only runs for full (candidate_types
        is None) extraction, since a caller that already narrowed the
        category set has made its own scoping decision.

        raise_on_error defaults to False (production behavior: degrade to []
        rather than crash a caller on a transient LLM/network failure).
        research/benchmark/evaluate_extraction.py passes True so it can tell
        a genuine "no clauses found" apart from a quota/network failure and
        stop a long batch run cleanly instead of burning through the rest of
        it on calls that are guaranteed to keep failing.

        enable_fallback controls the second call described above (see
        FALLBACK_CATEGORIES) - it only fires when the primary pass found
        none of those categories, so most contracts (which genuinely don't
        contain any of these rare clause types) still cost one LLM call, not
        two. Defaults to True for real accuracy; callers that need to
        conserve quota can pass False to get exactly the old single-pass
        behavior.

        Re-analyzing the same contract text previously re-billed the LLM
        every time (docs/ENTERPRISE_READINESS.md §9) - results are now
        cached on a hash of (prompt version, model, candidate_types,
        enable_fallback, text), so an identical request is free and instant
        on a cache hit.
        """
        if not (self._structured_llm or self.use_fallback) or not text or not text.strip():
            return []

        cache_key = self._cache_key(text, candidate_types, enable_fallback)
        if Phase3Config.CACHE_ENABLED:
            cached = cache.get(cache_key)
            if cached is not None:
                llm_usage_tracker.record_call("clause_extraction", self._model_name, cache_hit=True)
                return [ExtractedClause(**c) for c in cached]

        prompt = self._build_prompt(text, candidate_types)
        result = self._invoke(prompt, text, raise_on_error)

        if enable_fallback and candidate_types is None:
            found_types = {c.clause_type for c in result}
            missing = [t for t in FALLBACK_CATEGORIES if t not in found_types]
            if missing:
                fallback_prompt = self._build_fallback_prompt(text, missing)
                # raise_on_error deliberately NOT propagated from this second
                # call: a failure here means only the 8 rarest categories are
                # missing, not that extraction itself failed - degrading to
                # the primary pass's result is more useful than discarding it.
                result = result + self._invoke(fallback_prompt, text, raise_on_error=False)

        if Phase3Config.CACHE_ENABLED:
            cache.set(
                cache_key,
                [c.model_dump() for c in result],
                ttl=Phase3Config.get_cache_ttl("clause_extraction"),
            )

        return result

    async def extract_clauses_grouped(
        self,
        text: str,
        raise_on_error: bool = False,
    ) -> Tuple[List[ExtractedClause], Dict[str, str]]:
        """
        PILOT method, not called from any production path yet (see
        CATEGORY_GROUPS's module-level docstring and
        research/benchmark/pilot_grouped_extraction.py). Alternative to
        extract_clauses(): instead of one 41-category call (plus a
        conditional fallback call), runs CATEGORY_GROUPS's 7 groups as fully
        independent, concurrent LLM calls - each group's prompt only ever
        lists that group's categories, so a category is never competing
        with the other ~35 for the model's attention.

        Returns (clauses, group_failures). group_failures maps group_name ->
        error message for any group whose call failed - the other groups'
        results are still returned, not discarded, matching this project's
        honest-partial-failure discipline (node_status et al). raise_on_error
        controls only whether this method itself re-raises after every group
        has had a chance to run (the first group's failure, if any) - it does
        NOT abort other groups' in-flight calls, since one group failing is
        never a reason to discard results already obtained from the others.

        No caching, no PROMPT_VERSION cache-key involvement, no fallback
        pass, no usage of _CATEGORY_HINTS beyond what _build_prompt already
        applies per-category regardless of grouping - deliberately identical
        to extract_clauses() everywhere except the one variable under test
        (grouping), so a pilot comparison isolates that variable cleanly.
        """
        if not self._structured_llm or not text or not text.strip():
            return [], {}

        loop = asyncio.get_event_loop()

        async def run_group(executor: ThreadPoolExecutor, group_name: str, categories: List["CUADClauseType"]):
            prompt = self._build_prompt(text, categories)
            try:
                clauses = await loop.run_in_executor(
                    executor, self._invoke, prompt, text, True
                )
                return group_name, clauses, None
            except Exception as e:
                logger.error(f"Grouped extraction: group '{group_name}' failed: {e}")
                return group_name, [], str(e)

        # One shared pool, sized to the group count, so all 7 groups'
        # synchronous _invoke() calls (each already guarded by the real
        # llm_call_semaphore/circuit breaker inside _invoke itself) actually
        # run concurrently rather than one blocking the next.
        with ThreadPoolExecutor(max_workers=len(CATEGORY_GROUPS)) as executor:
            outcomes = await asyncio.gather(*[
                run_group(executor, name, categories) for name, categories in CATEGORY_GROUPS.items()
            ])

        results: List[ExtractedClause] = []
        failures: Dict[str, str] = {}
        for group_name, clauses, error in outcomes:
            results.extend(clauses)
            if error is not None:
                failures[group_name] = error

        if failures and raise_on_error:
            first_group, first_error = next(iter(failures.items()))
            raise RuntimeError(f"Grouped extraction: group '{first_group}' failed: {first_error}")

        return results, failures

    def _invoke(self, prompt: str, source_text: str, raise_on_error: bool) -> List[ExtractedClause]:
        """One structured-output LLM call, shared by the primary and
        fallback passes: identical guard rails (circuit breaker, semaphore,
        usage tracking, error handling), different prompt text only.

        When self.use_fallback is set, routes through invoke_with_fallback
        (backend/agents/llm_fallback_service.py) instead of a single bound
        LLM - Gemini first, then OpenAI, then Anthropic, whichever is
        actually configured and healthy right now. model_used is whatever
        provider actually served the call, not necessarily self._model_name
        (the constructor-time default), so usage tracking reflects reality."""
        model_used = self._model_name
        provider_used = PRIMARY_PROVIDER
        try:
            with llm_call_semaphore:
                if self._structured_llm is not None:
                    with GEMINI_CIRCUIT_BREAKER.guard():
                        raw_result = self._structured_llm.invoke(prompt)
                elif self.use_fallback:
                    raw_result, provider_used, model_used = invoke_with_fallback(
                        _LLMExtractionResponse, prompt, operation="clause_extraction"
                    )
                else:
                    return []
        except (CircuitBreakerOpenError, AllProvidersExhaustedError) as e:
            logger.warning(f"LLM clause extraction skipped - {e}")
            if raise_on_error:
                raise
            return []
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
            "clause_extraction", model_used, cache_hit=False, usage_metadata=usage_metadata,
            # is_fallback based on provider_used vs. PRIMARY_PROVIDER, not
            # model_used vs. self._model_name - see policy_evaluation_
            # service.py's identical fix for the real bug this avoids
            # (this file's DEFAULT_MODEL happening to equal the primary's
            # real model name masked the same bug here, accidentally, but
            # only for this one class - not something to rely on).
            is_fallback=(self.use_fallback and provider_used != PRIMARY_PROVIDER),
        )

        return [self._resolve_offsets(clause, source_text) for clause in response.clauses]

    def _cache_key(
        self,
        text: str,
        candidate_types: Optional[List[CUADClauseType]],
        enable_fallback: bool = True,
    ) -> str:
        types_part = ",".join(sorted(t.value for t in (candidate_types or [])))
        raw = f"clause_extraction:{PROMPT_VERSION}:{self._model_name}:{types_part}:{enable_fallback}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _build_prompt(self, text: str, candidate_types: Optional[List[CUADClauseType]]) -> str:
        types = candidate_types or list(CUADClauseType)
        type_list = "\n".join(
            f"- {t.value}: {_CATEGORY_HINTS[t]}" if t in _CATEGORY_HINTS else f"- {t.value}"
            for t in types
        )
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

    def _build_fallback_prompt(self, text: str, types: List[CUADClauseType]) -> str:
        """
        Narrow, example-backed follow-up prompt for FALLBACK_CATEGORIES -
        used only when the primary pass (all 41 categories at once) found
        none of them, since the benchmark showed the model essentially never
        attempting these categories in that dense a list. Fewer competing
        categories plus a worked example each is the intervention being
        tested here, not just a re-ask of the same question.
        """
        type_list = "\n".join(
            f"- {t.value}: {_FALLBACK_CATEGORY_GUIDANCE.get(t, '')}" for t in types
        )
        return f"""You are a contract analysis assistant. The categories below are rare,
easy-to-miss clause types. Carefully re-read the contract text below and
identify every clause that matches one of these CUAD clause categories:

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
