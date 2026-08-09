"""Server-authoritative provider/model capability registry.

Stable IDs are API/persistence identifiers. Provider API names stay internal to
the routing boundary, and configured status reveals only availability—not key
names, values, or credential failure detail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal


Workflow = Literal["chat", "analysis", "upload"]


class ModelSelectionError(ValueError):
    def __init__(self, message: str, category: str):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class ModelSpec:
    stable_id: str
    provider: str
    api_model: str
    display_label: str
    credential_env: str
    capabilities: frozenset[str]
    production_allowed: bool
    fallback_eligible: bool
    cost_class: str
    latency_class: str
    deprecated: bool = False

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.credential_env))

    def public(self) -> dict[str, Any]:
        return {
            "id": self.stable_id,
            "provider": self.provider,
            "display_label": self.display_label,
            "configured": self.configured,
            "capabilities": sorted(self.capabilities),
            "production_allowed": self.production_allowed,
            "fallback_eligible": self.fallback_eligible,
            "cost_class": self.cost_class,
            "latency_class": self.latency_class,
            "deprecated": self.deprecated,
        }


MODEL_SPECS = (
    ModelSpec(
        "gemini-2.5-flash", "google", "gemini-2.5-flash", "Google · Gemini 2.5 Flash",
        "GOOGLE_API_KEY",
        frozenset({"chat", "analysis", "upload", "structured_output", "tool_calling", "streaming", "vision"}),
        True, False, "low", "fast",
    ),
    ModelSpec(
        "gemini-2.5-pro", "google", "gemini-2.5-pro", "Google · Gemini 2.5 Pro",
        "GOOGLE_API_KEY",
        frozenset({"chat", "analysis", "upload", "structured_output", "tool_calling", "streaming", "vision"}),
        True, False, "medium", "slow",
    ),
    ModelSpec(
        "gpt-4o", "openai", "gpt-4o", "OpenAI · GPT-4o",
        "OPENAI_API_KEY",
        frozenset({"chat", "analysis", "upload", "structured_output", "tool_calling", "streaming", "vision"}),
        True, False, "high", "medium",
    ),
    ModelSpec(
        "claude-sonnet-5", "anthropic", "claude-sonnet-5", "Anthropic · Claude Sonnet 5",
        "ANTHROPIC_API_KEY",
        frozenset({"chat", "analysis", "upload", "structured_output", "tool_calling", "streaming", "vision"}),
        True, False, "high", "medium",
    ),
    ModelSpec(
        "mistral-large", "mistral", "mistral-large-latest", "Mistral · Large",
        "MISTRAL_API_KEY",
        frozenset({"chat", "tool_calling", "streaming"}),
        False, False, "medium", "medium",
    ),
)

_BY_ID = {spec.stable_id: spec for spec in MODEL_SPECS}
DEFAULT_MODEL = "gemini-2.5-flash"
FIXED_EMBEDDING = {
    "provider": "google",
    "model": "gemini-embedding-001",
    "dimensions": 1536,
    "user_selectable": False,
    "reason": "Neo4j vector indexes and stored embeddings require a fixed dimension-compatible model.",
}


def required_capabilities(workflow: Workflow) -> frozenset[str]:
    if workflow == "chat":
        return frozenset({"chat", "tool_calling", "streaming"})
    return frozenset({workflow, "structured_output"})


def validate_model(model_id: str, workflow: Workflow) -> ModelSpec:
    spec = _BY_ID.get(model_id)
    if not spec:
        raise ModelSelectionError("Unknown model", "unknown_model")
    if spec.deprecated:
        raise ModelSelectionError("Model is deprecated", "deprecated_model")
    required = required_capabilities(workflow)
    if not required.issubset(spec.capabilities):
        raise ModelSelectionError("Model is incompatible with this workflow", "incompatible_model")
    if not spec.configured:
        raise ModelSelectionError("Model provider is unavailable", "unconfigured_provider")
    if os.getenv("ENVIRONMENT", "development").lower() == "production" and not spec.production_allowed:
        raise ModelSelectionError("Model is not enabled for production", "production_disallowed")
    return spec


def available_models(workflow: Workflow) -> list[ModelSpec]:
    required = required_capabilities(workflow)
    production = os.getenv("ENVIRONMENT", "development").lower() == "production"
    return [
        spec for spec in MODEL_SPECS
        if spec.configured
        and not spec.deprecated
        and required.issubset(spec.capabilities)
        and (not production or spec.production_allowed)
    ]


def model_spec(model_id: str) -> ModelSpec:
    try:
        return _BY_ID[model_id]
    except KeyError as exc:
        raise ModelSelectionError("Unknown model", "unknown_model") from exc
