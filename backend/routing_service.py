"""Autonomous student/teacher chat-model router (Phase 6, MLOps governance).

Picks a low-cost "student" model for simple extraction prompts and a
high-reasoning "teacher" model for complex synthesis/redline prompts,
instead of every chat turn paying a fixed model's cost regardless of task
difficulty.

Deterministic keyword/length heuristic, not an embedding-similarity
classifier: a real semantic router would add an extra embedding-API call
(cost + latency) to every single chat turn just to decide which model
answers it, and its output would depend on a live provider call - not
acceptable for something that must classify before the real request the
user is paying for, and not something Playwright can assert on
deterministically. classify_complexity() below is intentionally simple and
inspectable so its behavior is provable, not just "usually right".

The router only ever resolves to an id already present in
backend.model_registry.MODEL_SPECS - it does not construct or validate
models itself. main.py's /api/run/ still calls validate_model() on
whatever this returns, same as it does for a manually-selected model.
"""

from __future__ import annotations

import re
from typing import Literal

Tier = Literal["student", "teacher"]

# The client-facing sentinel a caller sends as payload.model to opt into
# routing (backend/api/model_registry_api.py exposes it as a selectable
# "Auto" entry; backend/main.py's /api/run/ resolves it via
# route_chat_model() before it ever reaches model_registry.validate_model).
AUTO_MODEL_ID = "auto"

# Registry stable IDs (backend/model_registry.py) - kept in one place so
# main.py and tests share the same source of truth for which concrete
# models the "auto" pseudo-entry can resolve to.
STUDENT_MODEL_ID = "gemini-2.5-flash"
TEACHER_MODEL_ID = "claude-sonnet-5"

# Verbs/nouns that signal synthesis, drafting, negotiation, or multi-
# document reasoning - the class of task the user's spec calls out as
# needing the high-reasoning model ("redlines or synthesis"). Word-boundary
# matched so "redline" doesn't also match inside an unrelated word.
_TEACHER_SIGNAL_PATTERN = re.compile(
    r"\b("
    r"redline\w*|synthesi[sz]e?|negotiat\w*|strategy|strategic\w*|"
    r"comprehensive\w*|reconcile\w*|renegotiat\w*|rewrite\w*|"
    r"trade-?offs?|multi-?step|"
    r"draft(?:ing)?\s+(?:a|an|the)?\s*(?:response|counter|counterproposal|clause|amendment)|"
    r"compare\w*\s+\w+\s+across|risk\s+assessment"
    r")\b",
    re.IGNORECASE,
)

# Above this word count a prompt is treated as multi-part/complex even
# without an explicit teacher-signal keyword - a long, compound question
# is rarely "simple extraction".
_COMPLEXITY_WORD_COUNT_THRESHOLD = 60


def classify_complexity(prompt: str) -> Tier:
    """Deterministic tier classification for a single chat prompt.

    Empty/whitespace-only input is treated as "student" (nothing to reason
    about); this mirrors how such a prompt would already be rejected
    upstream by Prompt Guard before this would ever actually run.
    """
    if not prompt or not prompt.strip():
        return "student"
    if _TEACHER_SIGNAL_PATTERN.search(prompt):
        return "teacher"
    if len(prompt.split()) > _COMPLEXITY_WORD_COUNT_THRESHOLD:
        return "teacher"
    return "student"


def route_chat_model(prompt: str) -> tuple[str, Tier]:
    """Returns (registry stable_id, tier) for the model that should answer
    this prompt. Caller (main.py's /api/run/) still runs the resolved id
    through model_registry.validate_model() before use - this function
    only decides which of the two candidate ids to try."""
    tier = classify_complexity(prompt)
    model_id = TEACHER_MODEL_ID if tier == "teacher" else STUDENT_MODEL_ID
    return model_id, tier
