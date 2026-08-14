"""Relative score-delta retrieval filtering, replacing fixed absolute
similarity cutoffs (real gap this closes: three independent search
implementations each hardcoded their own absolute float - 0.3, 0.65, 0.8 -
for conceptually the same "is this candidate relevant enough" decision,
chosen individually per file/level rather than adapting to how confidently
the index actually matched a given query).

Standard "top-K then score-delta" cutoff: take the top DYNAMIC_RETRIEVAL_TOP_K
nearest-neighbor candidates by similarity, then keep only those within
DYNAMIC_RETRIEVAL_DELTA of the single best-scoring candidate for THIS query -
never below DYNAMIC_RETRIEVAL_FLOOR regardless of how tight or wide the
query's own score spread is. A query with one clearly-best match returns a
tight result set; a query where several chunks are nearly equally relevant
returns all of them, without a human having pre-guessed the "right" absolute
number for either case or for every search level.

Deliberately a pure, dependency-free function (no Neo4j/embedding imports):
callers do their own top-K retrieval (the vector index query already returns
candidates in score order) and pass the resulting candidate pool in; this
module only decides which of those candidates clear the relevance bar. Kept
in shared/utils rather than either search_strategies.py or
enhanced_contract_search_tool.py on purpose - both files use it independently
and keep their own separate Cypher, capabilities, caching, and return
contracts (AGENTS.md: those two implementations are intentionally separate,
not merged by this change).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence, TypeVar

DYNAMIC_RETRIEVAL_TOP_K = 15
DYNAMIC_RETRIEVAL_FLOOR = 0.35
DYNAMIC_RETRIEVAL_DELTA = 0.20

T = TypeVar("T", bound=Mapping[str, Any])


def apply_dynamic_score_filter(
    candidates: Sequence[T],
    score_key: str = "relevance_score",
    floor: float = DYNAMIC_RETRIEVAL_FLOOR,
    delta: float = DYNAMIC_RETRIEVAL_DELTA,
) -> list[T]:
    """Keep only candidates within `delta` of the best score present in this
    candidate set, never below `floor`. Assumes `candidates` is already the
    top-K pool (K itself is applied earlier, at the vector index query -
    this function only decides which of those K are relevant enough); an
    empty or missing-score candidate list returns unchanged/empty."""
    scored = [c for c in candidates if isinstance(c.get(score_key), (int, float))]
    if not scored:
        return []
    max_score = max(float(c[score_key]) for c in scored)
    cutoff = max(floor, max_score - delta)
    return [c for c in scored if float(c[score_key]) >= cutoff]
