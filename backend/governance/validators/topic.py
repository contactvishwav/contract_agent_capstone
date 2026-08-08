from typing import List, Optional, Dict, Any
from ..base import IGuardValidator, GuardResult

class TopicValidator(IGuardValidator):
    """Ensures the prompt stays within the domain of contract analysis.

    Real, confirmed bug found live during a full Contract Chat functional
    audit: this used to be an ALLOW-list gate - any message of 4+ words
    that didn't contain an exact match from a small, ~18-word legal-
    jargon list (never including ordinary words like "payment", "pay",
    "cost", "fee", "invoice", or "terms") was hard-rejected before ever
    reaching the LLM. Confirmed live: "What are the payment terms?" and
    "how much does this cost and when do I have to pay?" - both
    obviously, unambiguously about the user's own uploaded contract -
    were flatly rejected as "not related to contract analysis".

    An allow-list is the wrong shape for this check: real users paraphrase
    constantly, and no fixed keyword list can keep up with ordinary
    English. A DENY-list of unambiguously unrelated requests is far more
    robust - it only has to recognize genuinely off-topic asks (jokes,
    recipes, weather, general trivia/coding help), and everything else
    passes through to the real agent, which already has no tools capable
    of doing anything except searching the caller's own tenant-scoped
    contract data. Genuine malicious/security-bypass intent is IntentValidator's
    job (also fixed in this pass - see intent.py's docstring for the real
    bug that made it silently no-op on every real call), not this one's.
    """

    OFF_TOPIC_PHRASES = [
        "tell me a joke", "write a poem", "how to make a bomb",
        "python code for", "how do i cook", "weather in",
        "capital of", "who is the president", "write me a song",
        "recommend a movie", "recipe for", "translate this to",
        "what's the score", "sports scores", "stock price of",
        "how do i lose weight", "give me a workout",
    ]

    def validate(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        prompt_lower = input_text.lower()

        for phrase in self.OFF_TOPIC_PHRASES:
            if phrase in prompt_lower:
                return GuardResult(
                    is_safe=False,
                    violation_type="OUT_OF_SCOPE",
                    message=f"Request is outside the scope of contract analysis: '{phrase}'"
                )

        return GuardResult(is_safe=True)
