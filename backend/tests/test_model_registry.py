from unittest.mock import patch

import pytest

from backend.model_registry import (
    FIXED_EMBEDDING,
    ModelSpec,
    ModelSelectionError,
    available_models,
    model_spec,
    validate_model,
)


def test_registry_maps_stable_id_to_provider_api_name_without_exposing_it_publicly():
    spec = model_spec("claude-sonnet-5")
    assert spec.provider == "anthropic"
    assert spec.api_model == "claude-sonnet-5"
    assert "api_model" not in spec.public()
    assert "credential_env" not in spec.public()


def test_unknown_unconfigured_and_incompatible_models_fail_with_distinct_categories():
    with pytest.raises(ModelSelectionError) as unknown:
        validate_model("not-real", "chat")
    assert unknown.value.category == "unknown_model"

    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ModelSelectionError) as unavailable:
            validate_model("gpt-4o", "chat")
        assert unavailable.value.category == "unconfigured_provider"

    with patch.dict("os.environ", {"MISTRAL_API_KEY": "configured"}, clear=True):
        with pytest.raises(ModelSelectionError) as incompatible:
            validate_model("mistral-large", "analysis")
        assert incompatible.value.category == "incompatible_model"


def test_available_choices_are_workflow_compatible_and_configuration_derived():
    with patch.dict(
        "os.environ",
        {"GOOGLE_API_KEY": "configured", "OPENAI_API_KEY": "configured"},
        clear=True,
    ):
        chat_ids = [spec.stable_id for spec in available_models("chat")]
        analysis_ids = [spec.stable_id for spec in available_models("analysis")]
    # gemini-2.5-pro is excluded here even though GOOGLE_API_KEY is
    # configured - see test_gemini_2_5_pro_is_deprecated_and_excluded below
    # and ADR-006's addendum: confirmed live, Google rejects this project's
    # real key with a 404 ("no longer available to new users") despite
    # listing the model, so it must never be shown as selectable.
    assert chat_ids == ["gemini-2.5-flash", "gpt-4o"]
    assert analysis_ids == ["gemini-2.5-flash", "gpt-4o"]


def test_gemini_2_5_pro_is_deprecated_and_excluded():
    """Independent-audit finding #3 regression: Google's real API 404s this
    exact, correctly-named, catalog-listed model for this project's key
    ("no longer available to new users") - it must never be offered as a
    selectable choice, and selecting it directly must fail immediately and
    cleanly rather than reaching the provider and failing there."""
    with patch.dict(
        "os.environ", {"GOOGLE_API_KEY": "configured"}, clear=True,
    ):
        assert "gemini-2.5-pro" not in [spec.stable_id for spec in available_models("chat")]
        assert "gemini-2.5-pro" not in [spec.stable_id for spec in available_models("analysis")]

        with pytest.raises(ModelSelectionError) as error:
            validate_model("gemini-2.5-pro", "chat")
    assert error.value.category == "deprecated_model"


def test_deprecated_model_fails_with_distinct_category_even_if_configured():
    deprecated = ModelSpec(
        "retired-model", "google", "retired-api-model", "Retired model",
        "GOOGLE_API_KEY", frozenset({"chat", "tool_calling", "streaming"}),
        True, False, "low", "fast", deprecated=True,
    )
    with patch.dict("os.environ", {"GOOGLE_API_KEY": "configured"}, clear=True), patch.dict(
        "backend.model_registry._BY_ID", {"retired-model": deprecated}, clear=False
    ):
        with pytest.raises(ModelSelectionError) as error:
            validate_model("retired-model", "chat")
    assert error.value.category == "deprecated_model"


def test_embedding_model_is_fixed_and_dimensioned():
    assert FIXED_EMBEDDING == {
        "provider": "google",
        "model": "gemini-embedding-001",
        "dimensions": 1536,
        "user_selectable": False,
        "reason": "Neo4j vector indexes and stored embeddings require a fixed dimension-compatible model.",
    }
