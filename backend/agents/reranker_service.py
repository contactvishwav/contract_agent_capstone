"""
Real, second-stage re-ranking for semantic contract/clause search - one
batched Gemini structured-output call scoring an entire candidate pool's
relevance to the query, replacing "return the vector index's raw cosine-
similarity order" with a joint query+document relevance judgment.

Why Gemini and not a local cross-encoder (sentence-transformers): measured
directly, not estimated. Loading cross-encoder/ms-marco-MiniLM-L-6-v2 (the
standard lightweight reranking model) and running one real batched
inference (query x 30 candidates) cost ~209MB of real RSS, on top of
torch's own ~300MB+ import overhead - against the real GCP e2-micro
production VM's already-tight budget (docker-compose.prod.yml: backend/
worker/ui/caddy/redis mem_limits already sum to ~844MB of the box's 1GB,
per docs/DEPLOYMENT.md's resource-fit table), this does not fit without
real risk of destabilizing the live system. Gemini re-ranking adds zero
incremental deployed memory (no new library loaded into either container -
langchain-google-genai is already imported and initialized for extraction/
policy evaluation) and reuses the exact same circuit-breaker/semaphore/
usage-tracking infrastructure already protecting every other Gemini call
in this codebase, at the cost of real network latency per call (mitigated
by batching every candidate into one request and a short, enforced
timeout) and a small real, honestly-tracked API cost.

Mirrors LLMExtractionService/PolicyEvaluationService's established
discipline throughout: structured output (never trust free text), a
short-timeout dedicated LLM client (never let one slow call degrade the
whole search response), a circuit breaker (never keep calling a dependency
that's already failing), and independent verification of the model's
output (never trust an LLM-returned index without checking it against the
real input list - same principle as offset re-verification in
llm_extraction_service.py and rule_id verification in
policy_evaluation_service.py).
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.shared.monitoring.llm_usage_tracker import llm_usage_tracker
from backend.shared.monitoring.latency_tracker import track_latency
from backend.shared.reliability.circuit_breaker import RERANKER_CIRCUIT_BREAKER, CircuitBreakerOpenError
from backend.shared.utils.llm_concurrency import llm_call_semaphore
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

# Real, enforced HTTP-level timeout (langchain_google_genai's own
# request_timeout, not a wrapper around the call) - a search request is a
# synchronous, user-facing path, so re-ranking must never be the reason a
# search takes noticeably longer than an unranked one. Deliberately far
# shorter than get_default_llm's 120s (that budget is for a multi-thousand-
# token extraction call this system is willing to wait on; re-ranking a
# batch of short candidate snippets is a much smaller, much faster task,
# and a slow response here is exactly the case that should fall back
# rather than be waited out).
#
# 12s, not something closer to 4-5s: found live, while writing this
# module's own tests, that the real Gemini API rejects any deadline under
# 10s outright - "Manually set deadline 4s is too short. Minimum allowed
# deadline is 10s." (a real 400 INVALID_ARGUMENT, not a timeout at all) -
# every real re-ranking call would have failed unconditionally at a
# shorter value. 12s keeps a margin above that real, discovered floor
# while still being far short of get_default_llm's 120s.
RERANKER_TIMEOUT_SECONDS = float(os.getenv("RERANKER_TIMEOUT_SECONDS", "12.0"))


def get_reranker_llm(model: str = DEFAULT_MODEL):
    """
    Lazily construct the re-ranking LLM. Returns None if no GOOGLE_API_KEY
    is configured, matching llm_extraction_service.get_default_llm's
    identical rationale (callers can construct a RerankerService eagerly
    without crashing before any key is present).
    """
    if not os.getenv("GOOGLE_API_KEY"):
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=model, temperature=0, request_timeout=RERANKER_TIMEOUT_SECONDS, max_retries=1
    )


class _RerankedItem(BaseModel):
    index: int = Field(
        description="The candidate's index in the original numbered list, copied exactly - never invented"
    )
    relevance_score: float = Field(
        ge=0.0, le=1.0, description="How relevant this candidate is to the query: 0.0=irrelevant, 1.0=highly relevant"
    )


class _RerankResponse(BaseModel):
    rankings: List[_RerankedItem] = Field(default_factory=list)


@dataclass
class RerankOutcome:
    """
    results: candidates (shallow-copied dicts) with three fields injected -
    original_rank (1-indexed position before re-ranking), reranked_rank
    (1-indexed position after), and relevance_score (the model's score, or
    None for any candidate the model didn't score / when reranked=False) -
    truncated to top_k. Always populated, whether or not reranking actually
    ran, so a caller never has to branch on `reranked` just to get results.

    reranked: whether the LLM call actually happened and produced usable
    output. False on ANY failure (circuit open, timeout, malformed
    response, no candidates, no LLM configured) - `results` in that case is
    simply `candidates[:top_k]` in original order, original_rank ==
    reranked_rank for every item. Search must never hard-fail because
    re-ranking failed.

    reason: set only when reranked is False - a short machine-readable
    cause (e.g. "circuit_open", "timeout_or_error", "no_candidates"),
    surfaced in the API response's explainability block so a caller/UI can
    distinguish "reranking wasn't attempted" from "it ran and this is
    genuinely the best order."
    """
    results: List[Dict[str, Any]]
    reranked: bool
    reason: Optional[str] = None


class RerankerService:
    """
    One batched call per rerank() invocation, never one call per candidate -
    the entire candidate pool is scored in a single structured-output
    request. Stateless and tenant-agnostic by construction: it operates
    only on whatever candidate list its caller passes in, with no Neo4j
    access and no notion of tenant_id at all - tenant isolation is enforced
    entirely by the caller only ever passing an already tenant-filtered
    candidate pool (search_strategies.py's Cypher WHERE c.tenant_id =
    $tenant_id runs before reranker.rerank() is ever called), not by
    anything in this class. See test_reranker_service.py's
    TenantIsolationTests for the concrete proof.
    """

    def __init__(self, llm=None):
        # No internal fallback to get_reranker_llm() here, deliberately -
        # matches LLMExtractionService/PolicyEvaluationService's identical
        # convention: the caller resolves the LLM (typically via
        # get_reranker_llm()) and passes it in, so "no LLM configured" is
        # an explicit, observable llm=None a caller chose, never a silent
        # env-var lookup this class does behind a caller's back.
        self.llm = llm
        self._model_name = getattr(llm, "model", None) or "unknown"
        self._structured_llm = (
            llm.with_structured_output(_RerankResponse, include_raw=True) if llm else None
        )

    @track_latency("search_reranking")
    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        text_key: str,
        top_k: int,
    ) -> RerankOutcome:
        """
        query: the user's real search query text.
        candidates: already tenant-filtered, already-fetched rows (e.g.
            Contract or Clause dicts) - the CANDIDATE POOL (wider than
            top_k; see RERANK_POOL_SIZE in vector_index_config.py).
        text_key: which field of each candidate dict holds the text to
            judge relevance against (e.g. "summary" for contracts,
            "content" for clauses) - kept generic so this one class serves
            every search level rather than one reranker per level.
        top_k: how many results to actually return after re-ranking.
        """
        if not candidates:
            return self._fallback(candidates, top_k, "no_candidates")
        if not self._structured_llm:
            return self._fallback(candidates, top_k, "no_llm_configured")

        prompt = self._build_prompt(query, candidates, text_key)

        try:
            with llm_call_semaphore:
                with RERANKER_CIRCUIT_BREAKER.guard():
                    raw_result = self._structured_llm.invoke(prompt)
        except CircuitBreakerOpenError as e:
            logger.warning(f"Re-ranking skipped - {e}")
            return self._fallback(candidates, top_k, "circuit_open")
        except Exception as e:
            # Covers a real timeout (RERANKER_TIMEOUT_SECONDS) the same as
            # any other failure - langchain_google_genai raises a timeout
            # as a normal exception here, not a distinct type, so a slow
            # call degrades exactly like a broken one: fall back, don't hang.
            logger.error(f"Re-ranking failed: {e}")
            return self._fallback(candidates, top_k, "timeout_or_error")

        response = raw_result.get("parsed")
        if response is None:
            logger.error(f"Re-ranking returned no parsed result (parsing_error={raw_result.get('parsing_error')})")
            return self._fallback(candidates, top_k, "parse_failed")

        usage_metadata = getattr(raw_result.get("raw"), "usage_metadata", None)
        llm_usage_tracker.record_call(
            "search_reranking", self._model_name, cache_hit=False, usage_metadata=usage_metadata
        )

        return self._apply_rankings(candidates, response.rankings, top_k)

    def _build_prompt(self, query: str, candidates: List[Dict[str, Any]], text_key: str) -> str:
        numbered = "\n".join(
            f"{i}. {(c.get(text_key) or '')[:1000]}" for i, c in enumerate(candidates)
        )
        return f"""You are a legal search relevance judge. Given a search query and a
numbered list of candidate contract excerpts, score how relevant EACH
candidate is to the query, from 0.0 (irrelevant) to 1.0 (highly relevant).
Judge substantive relevance to the query's meaning, not just keyword
overlap. Score every index listed below - do not omit any.

Query: {query}

Candidates:
{numbered}"""

    def _apply_rankings(
        self, candidates: List[Dict[str, Any]], rankings: List[_RerankedItem], top_k: int
    ) -> RerankOutcome:
        n = len(candidates)
        scores: Dict[int, float] = {}
        for item in rankings:
            # Never trust an LLM-returned index without checking it against
            # the real input list - the same discipline already applied to
            # offsets (llm_extraction_service._find_span) and rule_id
            # (policy_evaluation_service.evaluate_clause). A hallucinated
            # out-of-range index is discarded, not indexed into candidates.
            if 0 <= item.index < n:
                scores[item.index] = item.relevance_score
            else:
                logger.warning(f"Re-ranking: discarding out-of-range index {item.index} (valid: 0-{n - 1})")

        # Candidates the model scored, best first; any candidate it silently
        # omitted keeps its original relative order and is appended after -
        # graceful degradation at the individual-candidate level, not just
        # all-or-nothing for the whole call.
        scored_indices = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)
        unscored_indices = [i for i in range(n) if i not in scores]
        final_order = scored_indices + unscored_indices

        results = []
        for reranked_rank, orig_idx in enumerate(final_order[:top_k], start=1):
            item = dict(candidates[orig_idx])
            item["original_rank"] = orig_idx + 1
            item["reranked_rank"] = reranked_rank
            item["relevance_score"] = scores.get(orig_idx)
            results.append(item)

        return RerankOutcome(results=results, reranked=True)

    @staticmethod
    def _fallback(candidates: List[Dict[str, Any]], top_k: int, reason: str) -> RerankOutcome:
        results = []
        for i, c in enumerate(candidates[:top_k]):
            item = dict(c)
            item["original_rank"] = i + 1
            item["reranked_rank"] = i + 1
            item["relevance_score"] = None
            results.append(item)
        return RerankOutcome(results=results, reranked=False, reason=reason)
