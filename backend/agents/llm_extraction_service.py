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

import os
import re
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


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
    return ChatGoogleGenerativeAI(model=model, temperature=0, request_timeout=120)


class LLMExtractionService:
    """
    Owns the prompt, the structured-output call, and response handling for
    LLM-based CUAD clause extraction. Single-pass: one call per invocation
    asks Gemini to identify every applicable clause type in the given text.
    """

    def __init__(self, llm=None):
        self.llm = llm
        self._structured_llm = llm.with_structured_output(_LLMExtractionResponse) if llm else None

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
        """
        if not self._structured_llm or not text or not text.strip():
            return []

        prompt = self._build_prompt(text, candidate_types)

        try:
            response = self._structured_llm.invoke(prompt)
        except Exception as e:
            logger.error(f"LLM clause extraction failed: {e}")
            if raise_on_error:
                raise
            return []

        return [self._resolve_offsets(clause, text) for clause in response.clauses]

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
