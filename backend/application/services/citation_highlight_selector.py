"""Narrows a citation's up-to-1200-character retrieved excerpt down to the
specific sentence(s) that actually support the generated answer, for PDF
highlighting only.

Root cause this fixes: chat_evidence_service.py's excerpt (grounding/
"Cited passage" evidence, capped at MAX_EXCERPT_LENGTH=1200 chars) was
being used verbatim as PdfCitationViewer's highlight target. A one-
sentence answer therefore highlighted up to a full page of the PDF - the
uniqueHighlightItemIndexes matching itself was always exact/precise, but
the span it was asked to match was the entire chunk, not the claim.

Design choice (real decision, not a one-line fix - see the two options
weighed below):

(a) Have the answer-generation call self-report which exact sentence(s)
    it used, e.g. via a structured per-citation quote field. Rejected:
    it would grow the generation contract (today a single freeform
    streamed-text call) into something that also emits verified quote
    spans, which either means a second model call (ADR-007 already
    rejects an answer-rewrite call for the same cost/latency/new-
    unsupported-claim-surface reasons) or asking the existing call to
    self-attest a quote it could paraphrase - another LLM output to
    distrust and re-validate, not fewer.
(b) A deterministic, non-LLM narrowing step applied after generation,
    using data already in hand: the final validated answer text (real,
    available at both citation-build call sites - main.py's runner()
    after Output Guard resolves final_ai_content, and chat_sessions.py's
    restore path via the persisted message's own content) and the
    already-retrieved excerpt. No new model call, no growth of the
    generation contract, consistent with this codebase's stated
    preference for deterministic checks over added LLM calls (ADR-007).
    Chosen.

This module implements (b). It is intentionally conservative: it only
narrows when there is real, confident lexical overlap between a specific
excerpt sentence and the answer; otherwise it returns None and the
caller keeps today's behavior (the full excerpt as the highlight
target), never guessing at a possibly-wrong span. This composes safely
with ADR-005's existing "no fuzzy first-match" exact-match-or-degrade
tier system: whatever this module selects still has to be found as a
single, unique, exact, whitespace-normalized substring on the PDF page
before it's ever shown as an exact highlight - if it isn't (e.g. because
sentence splitting produced something that doesn't survive PDF text-
layer extraction identically), the existing fallback tiers already
handle graceful degradation, and locate_unique_page_match's caller
retries against the untouched full excerpt.
"""

from __future__ import annotations

import re
from typing import Optional

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_WORD = re.compile(r"[a-z0-9]+")

# Deliberately small and generic (not legal-domain-tuned) - only the most
# semantically empty function words, so domain-meaningful terms common in
# contracts ("shall", "within", "notice", "days") still count toward overlap.
_STOPWORDS = frozenset({
    "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "the", "this", "that", "it", "its", "be", "with", "for", "by", "as",
    "at", "on", "from", "not",
})

# A narrowed highlight must be backed by real, confident overlap - both a
# minimum fraction of the sentence's own content words appearing in the
# answer, and a minimum absolute count, so a short, generic sentence
# ("3. Indemnification.") can't spuriously "match" a long answer just by
# chance. Below this bar, we don't guess - see module docstring.
_MIN_OVERLAP_RATIO = 0.5
_MIN_OVERLAP_TOKENS = 3

# Safety cap on how many adjacent sentences can be joined into one
# highlight span, so this can never regress toward "highlight nearly
# everything" even in a pathological case.
_MAX_JOINED_SENTENCES = 3
_MAX_HIGHLIGHT_CHARS = 500


def _tokens(text: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS)


def _split_sentences(excerpt: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(excerpt.strip()) if s.strip()]


def _overlap_score(sentence_tokens: frozenset[str], answer_tokens: frozenset[str]) -> float:
    if not sentence_tokens:
        return 0.0
    return len(sentence_tokens & answer_tokens) / len(sentence_tokens)


def select_claim_highlight(excerpt: Optional[str], answer_text: Optional[str]) -> Optional[str]:
    """Return the narrowest contiguous span of `excerpt` that has confident
    lexical support in `answer_text`, or None if no sentence clears the
    confidence bar (caller should fall back to the full excerpt)."""
    if not isinstance(excerpt, str) or not excerpt.strip():
        return None
    if not isinstance(answer_text, str) or not answer_text.strip():
        return None

    sentences = _split_sentences(excerpt)
    if len(sentences) <= 1:
        return None

    answer_tokens = _tokens(answer_text)
    if not answer_tokens:
        return None

    scores = []
    for sentence in sentences:
        sentence_tokens = _tokens(sentence)
        overlap = len(sentence_tokens & answer_tokens)
        ratio = _overlap_score(sentence_tokens, answer_tokens)
        qualifies = ratio >= _MIN_OVERLAP_RATIO and overlap >= _MIN_OVERLAP_TOKENS
        scores.append(ratio if qualifies else -1.0)

    if max(scores) < 0:
        return None

    anchor = max(range(len(sentences)), key=lambda i: scores[i])
    selected = [anchor]

    # Expand to immediately adjacent sentences only while they also
    # qualify - never join non-adjacent sentences, since the joined
    # result must remain a single contiguous substring of the real page
    # text for locate_unique_page_match's exact search to find it at all.
    left, right = anchor - 1, anchor + 1
    while len(selected) < _MAX_JOINED_SENTENCES:
        grew = False
        if right < len(sentences) and scores[right] >= 0:
            selected.append(right)
            right += 1
            grew = True
        if len(selected) < _MAX_JOINED_SENTENCES and left >= 0 and scores[left] >= 0:
            selected.insert(0, left)
            left -= 1
            grew = True
        if not grew:
            break

    selected.sort()
    span = " ".join(sentences[i] for i in selected)
    if len(span) > _MAX_HIGHLIGHT_CHARS:
        return sentences[anchor]
    return span
