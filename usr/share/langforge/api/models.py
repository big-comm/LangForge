"""Curated production model catalog.

Prices are USD per one million tokens and were verified against provider
documentation on 2026-07-25.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Stable metadata for one provider model."""

    provider: str
    model_id: str
    label: str
    role: str
    input_price: float
    cached_input_price: float | None
    output_price: float
    context_window: int | None = None
    max_output_tokens: int | None = None
    recommended: bool = False
    request_profile: str = "chat"

    @property
    def id(self) -> str:
        """Compatibility alias used by model pickers."""
        return self.model_id

    @property
    def token_pricing(self) -> tuple[float, float]:
        """Return the uncached input/output prices expected by TranslationAPI."""
        return (self.input_price, self.output_price)

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float:
        """Estimate request cost using provider-published token rates."""
        cached = max(0, min(input_tokens, cached_input_tokens))
        uncached = max(0, input_tokens - cached)
        cached_price = (
            self.input_price
            if self.cached_input_price is None
            else self.cached_input_price
        )
        return (
            uncached * self.input_price
            + cached * cached_price
            + max(0, output_tokens) * self.output_price
        ) / 1_000_000


_CATALOG: dict[str, tuple[ModelSpec, ...]] = {
    "groq": (
        ModelSpec(
            "groq",
            "openai/gpt-oss-120b",
            "GPT-OSS 120B",
            "quality",
            0.15,
            0.075,
            0.60,
            131_072,
            65_536,
            True,
            "groq-gpt-oss",
        ),
        ModelSpec(
            "groq",
            "openai/gpt-oss-20b",
            "GPT-OSS 20B",
            "economy",
            0.075,
            0.0375,
            0.30,
            131_072,
            65_536,
            False,
            "groq-gpt-oss",
        ),
    ),
    "gemini-free": (
        ModelSpec(
            "gemini-free",
            "gemini-3.5-flash-lite",
            "Gemini 3.5 Flash-Lite",
            "economy",
            0.30,
            0.03,
            2.50,
            1_048_576,
            65_536,
            True,
            "gemini-3-flash-lite",
        ),
        ModelSpec(
            "gemini-free",
            "gemini-3.6-flash",
            "Gemini 3.6 Flash",
            "quality",
            1.50,
            0.15,
            7.50,
            1_048_576,
            65_536,
            False,
            "gemini-3-flash",
        ),
    ),
    "openrouter": (
        ModelSpec(
            "openrouter",
            "openai/gpt-oss-120b:free",
            "GPT-OSS 120B (Free)",
            "quality",
            0.0,
            0.0,
            0.0,
            131_072,
            65_536,
            True,
            "openrouter-gpt-oss",
        ),
        ModelSpec(
            "openrouter",
            "openai/gpt-oss-20b:free",
            "GPT-OSS 20B (Free)",
            "economy",
            0.0,
            0.0,
            0.0,
            131_072,
            65_536,
            False,
            "openrouter-gpt-oss",
        ),
        ModelSpec(
            "openrouter",
            "openrouter/free",
            "Free Models Router",
            "fallback",
            0.0,
            0.0,
            0.0,
            recommended=False,
            request_profile="openrouter-free",
        ),
    ),
    "mistral-free": (
        ModelSpec(
            "mistral-free",
            "mistral-small-latest",
            "Mistral Small 4",
            "balanced",
            0.15,
            0.015,
            0.60,
            256_000,
            recommended=True,
            request_profile="mistral-reasoning-none",
        ),
        ModelSpec(
            "mistral-free",
            "mistral-large-latest",
            "Mistral Large 3",
            "quality",
            0.50,
            0.05,
            1.50,
            request_profile="mistral-chat",
        ),
        ModelSpec(
            "mistral-free",
            "mistral-medium-latest",
            "Mistral Medium 3.5",
            "quality",
            1.50,
            0.15,
            7.50,
            256_000,
            request_profile="mistral-reasoning-none",
        ),
    ),
    "openai": (
        ModelSpec(
            "openai",
            "gpt-5.6-sol",
            "GPT-5.6 Sol",
            "quality",
            5.00,
            0.50,
            30.00,
            1_050_000,
            128_000,
            False,
            "openai-no-reasoning",
        ),
        ModelSpec(
            "openai",
            "gpt-5.6-terra",
            "GPT-5.6 Terra",
            "balanced",
            2.50,
            0.25,
            15.00,
            1_050_000,
            128_000,
            False,
            "openai-no-reasoning",
        ),
        ModelSpec(
            "openai",
            "gpt-5.6-luna",
            "GPT-5.6 Luna",
            "economy",
            1.00,
            0.10,
            6.00,
            1_050_000,
            128_000,
            True,
            "openai-no-reasoning",
        ),
        ModelSpec(
            "openai",
            "gpt-5.4-mini",
            "GPT-5.4 mini",
            "economy",
            0.75,
            0.075,
            4.50,
            400_000,
            128_000,
            False,
            "openai-no-reasoning",
        ),
        ModelSpec(
            "openai",
            "gpt-5.4-nano",
            "GPT-5.4 nano",
            "ultra-economy",
            0.20,
            0.02,
            1.25,
            400_000,
            128_000,
            False,
            "openai-no-reasoning",
        ),
    ),
    "gemini": (
        ModelSpec(
            "gemini",
            "gemini-3.5-flash-lite",
            "Gemini 3.5 Flash-Lite",
            "economy",
            0.30,
            0.03,
            2.50,
            1_048_576,
            65_536,
            True,
            "gemini-3-flash-lite",
        ),
        ModelSpec(
            "gemini",
            "gemini-3.6-flash",
            "Gemini 3.6 Flash",
            "quality",
            1.50,
            0.15,
            7.50,
            1_048_576,
            65_536,
            False,
            "gemini-3-flash",
        ),
    ),
    "grok": (
        ModelSpec(
            "grok",
            "grok-4.3",
            "Grok 4.3",
            "balanced",
            1.25,
            0.20,
            2.50,
            1_000_000,
            recommended=True,
            request_profile="grok-no-reasoning",
        ),
        ModelSpec(
            "grok",
            "grok-4.5",
            "Grok 4.5",
            "quality",
            2.00,
            0.30,
            6.00,
            500_000,
            recommended=False,
            request_profile="grok-low-reasoning",
        ),
    ),
    "deepseek": (
        ModelSpec(
            "deepseek",
            "deepseek-v4-flash",
            "DeepSeek V4 Flash",
            "economy",
            0.14,
            0.0028,
            0.28,
            1_000_000,
            384_000,
            True,
            "deepseek-no-thinking",
        ),
        ModelSpec(
            "deepseek",
            "deepseek-v4-pro",
            "DeepSeek V4 Pro",
            "quality",
            0.435,
            0.003625,
            0.87,
            1_000_000,
            384_000,
            False,
            "deepseek-no-thinking",
        ),
    ),
}

_ALIASES: dict[str, dict[str, str]] = {
    "groq": {
        "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
        "llama-3.1-8b-instant": "openai/gpt-oss-20b",
        "gemma2-9b-it": "openai/gpt-oss-20b",
        "mixtral-8x7b-32768": "openai/gpt-oss-20b",
    },
    "gemini-free": {
        "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
        "gemini-2.5-flash": "gemini-3.6-flash",
    },
    "openrouter": {
        "meta-llama/llama-3.1-8b-instruct:free": "openai/gpt-oss-20b:free",
        "meta-llama/llama-3.3-70b-instruct:free": "openai/gpt-oss-120b:free",
        "google/gemma-2-9b-it:free": "openai/gpt-oss-20b:free",
        "mistralai/mistral-7b-instruct:free": "openai/gpt-oss-20b:free",
    },
    "mistral-free": {
        "open-mistral-7b": "mistral-small-latest",
        "open-mixtral-8x7b": "mistral-small-latest",
    },
    "openai": {
        "gpt-5.6": "gpt-5.6-luna",
        "gpt-5": "gpt-5.6-luna",
        "gpt-5-mini": "gpt-5.4-mini",
        "gpt-5-nano": "gpt-5.4-nano",
        "gpt-4.1": "gpt-5.6-luna",
        "gpt-4.1-mini": "gpt-5.4-nano",
        "gpt-4.1-nano": "gpt-5.4-nano",
        "gpt-4o": "gpt-5.6-luna",
        "gpt-4o-mini": "gpt-5.4-nano",
        "gpt-4": "gpt-5.6-luna",
        "gpt-4-turbo": "gpt-5.6-luna",
        "gpt-3.5-turbo": "gpt-5.4-nano",
    },
    "gemini": {
        "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
        "gemini-2.5-flash": "gemini-3.6-flash",
        "gemini-2.5-pro": "gemini-3.6-flash",
        "gemini-2.0-flash": "gemini-3.6-flash",
        "gemini-2.0-flash-exp": "gemini-3.6-flash",
    },
    "grok": {
        "grok-4.3-latest": "grok-4.3",
        "grok-latest": "grok-4.3",
        "grok-4-fast": "grok-4.3",
        "grok-3-fast": "grok-4.3",
        "grok-3-mini-fast": "grok-4.3",
        "grok-3": "grok-4.3",
        "grok-2": "grok-4.3",
    },
    "deepseek": {
        "deepseek-chat": "deepseek-v4-flash",
        "deepseek-reasoner": "deepseek-v4-flash",
    },
}

_DEFAULTS = {
    "groq": "openai/gpt-oss-120b",
    "gemini-free": "gemini-3.5-flash-lite",
    "openrouter": "openai/gpt-oss-120b:free",
    "mistral-free": "mistral-small-latest",
    "openai": "gpt-5.6-luna",
    "gemini": "gemini-3.5-flash-lite",
    "grok": "grok-4.3",
    "deepseek": "deepseek-v4-flash",
}

MODEL_CATALOG: Mapping[str, tuple[ModelSpec, ...]] = MappingProxyType(_CATALOG)
MODEL_ALIASES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {provider: MappingProxyType(aliases) for provider, aliases in _ALIASES.items()}
)
MODEL_DEFAULTS: Mapping[str, str] = MappingProxyType(_DEFAULTS)


def models_for(provider: str) -> tuple[ModelSpec, ...]:
    """Return the curated models for a factory provider."""
    return MODEL_CATALOG.get(provider, ())


def model_ids(provider: str) -> tuple[str, ...]:
    """Return canonical model IDs for a factory provider."""
    return tuple(spec.model_id for spec in models_for(provider))


def default_model(provider: str) -> str:
    """Return a provider's default model, or an empty string if none applies."""
    return MODEL_DEFAULTS.get(provider, "")


def normalize_model(provider: str, model_id: str | None) -> str:
    """Normalize aliases and reject unknown IDs for curated providers."""
    candidate = (model_id or "").strip()
    if not candidate:
        return default_model(provider)
    specs = models_for(provider)
    if not specs:
        return candidate
    canonical = MODEL_ALIASES.get(provider, {}).get(candidate, candidate)
    if any(spec.model_id == canonical for spec in specs):
        return canonical
    return default_model(provider)


def get_model(provider: str, model_id: str | None) -> ModelSpec | None:
    """Return metadata for a canonical or aliased model."""
    canonical = normalize_model(provider, model_id)
    return next(
        (spec for spec in models_for(provider) if spec.model_id == canonical),
        None,
    )


def is_recommended(provider: str, model_id: str | None) -> bool:
    """Return whether a canonical or aliased model is recommended."""
    spec = get_model(provider, model_id)
    return bool(spec and spec.recommended)
