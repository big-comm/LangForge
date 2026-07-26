"""Tests for the curated provider model catalog."""

from dataclasses import FrozenInstanceError

import pytest

from api.models import (
    default_model,
    get_model,
    is_recommended,
    model_ids,
    models_for,
    normalize_model,
)


def test_current_model_portfolios():
    assert model_ids("groq") == (
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    )
    assert model_ids("openai") == (
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    )
    assert model_ids("deepseek") == (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    )
    assert model_ids("gemini") == (
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
    )
    assert model_ids("openrouter") == (
        "openai/gpt-oss-120b:free",
        "openai/gpt-oss-20b:free",
        "openrouter/free",
    )
    assert model_ids("mistral-free") == (
        "mistral-small-latest",
        "mistral-large-latest",
        "mistral-medium-latest",
    )
    assert model_ids("grok") == ("grok-4.3", "grok-4.5")


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("groq", "openai/gpt-oss-120b"),
        ("openai", "gpt-5.6-luna"),
        ("deepseek", "deepseek-v4-flash"),
        ("gemini-free", "gemini-3.5-flash-lite"),
        ("gemini", "gemini-3.5-flash-lite"),
        ("openrouter", "openai/gpt-oss-120b:free"),
        ("mistral-free", "mistral-small-latest"),
        ("grok", "grok-4.3"),
    ],
)
def test_translation_defaults_are_recommended(provider, expected):
    assert default_model(provider) == expected
    assert is_recommended(provider, expected)


@pytest.mark.parametrize(
    ("provider", "legacy", "canonical"),
    [
        ("groq", "llama-3.3-70b-versatile", "openai/gpt-oss-120b"),
        ("openai", "gpt-4o-mini", "gpt-5.4-nano"),
        ("deepseek", "deepseek-chat", "deepseek-v4-flash"),
        ("gemini", "gemini-2.0-flash-exp", "gemini-3.6-flash"),
        ("grok", "grok-4-fast", "grok-4.3"),
        (
            "openrouter",
            "meta-llama/llama-3.1-8b-instruct:free",
            "openai/gpt-oss-20b:free",
        ),
        ("mistral-free", "open-mixtral-8x7b", "mistral-small-latest"),
    ],
)
def test_legacy_aliases_are_normalized(provider, legacy, canonical):
    assert normalize_model(provider, legacy) == canonical


def test_empty_and_unknown_models_are_safe():
    assert normalize_model("openai", "") == default_model("openai")
    assert normalize_model("openai", "retired-model") == default_model("openai")
    assert normalize_model("openrouter", "removed/model:free") == default_model(
        "openrouter"
    )
    assert default_model("libretranslate") == ""
    assert models_for("libretranslate") == ()
    assert model_ids("unknown") == ()
    assert normalize_model("libretranslate", "custom-endpoint-model") == (
        "custom-endpoint-model"
    )


def test_model_spec_exposes_prices_and_is_immutable():
    spec = get_model("deepseek", "deepseek-v4-flash")
    assert spec is not None
    assert spec.id == spec.model_id
    assert spec.token_pricing == (0.14, 0.28)
    assert spec.estimate_cost(1_000_000, 1_000_000, 500_000) == pytest.approx(0.3514)
    with pytest.raises(FrozenInstanceError):
        spec.model_id = "changed"


def test_gemini_cached_input_uses_discounted_price():
    spec = get_model("gemini", "gemini-3.5-flash-lite")

    assert spec is not None
    assert spec.cached_input_price == 0.03
    assert spec.estimate_cost(1_000_000, 0, 1_000_000) == pytest.approx(0.03)
