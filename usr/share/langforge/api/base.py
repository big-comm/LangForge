"""Interface abstrata para APIs de tradução."""

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from core.languages import SUPPORTED_LANGUAGES

log = logging.getLogger(__name__)

# Retry settings for rate-limited requests
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 2.0  # seconds
_BACKOFF_FACTOR = 2.0

# Shared prompt template for all LLM-based translation APIs.
# Placeholders: {source}, {target}, {app_name}, {context_section}


def _resolve_lang(code: str) -> str:
    """Resolve a language code to its full name for clearer LLM prompts."""
    return SUPPORTED_LANGUAGES.get(code, code)
_TRANSLATION_PROMPT = (
    "You are a professional translator specializing in software localization. "
    "You are translating UI strings for the application '{app_name}'. "
    "Translate the following text from {source} to {target}.\n\n"
    "CRITICAL RULES:\n"
    "1. NEVER translate the application name '{app_name}' or any variation of it — "
    "it is a proper noun and must remain exactly as written.\n"
    "2. NEVER translate brand names, product names, project names, or proper nouns.\n"
    "3. Use natural, contextual translation appropriate for a software UI — "
    "do NOT translate word-by-word or literally.\n"
    "4. Adapt idioms and expressions to sound natural in the target language.\n"
    "5. Preserve any XML tags like <x1/>, <x2/>, placeholders like "
    "{{}}, %s, %d, and formatting codes exactly as they are.\n"
    "6. Return ONLY the translated text, nothing else.\n"
    "{context_section}"
)

# Backward-compatible alias (deprecated — prefer build_translation_prompt)
TRANSLATION_PROMPT = (
    "You are a professional translator specializing in software localization. "
    "Translate the following text from {source} to {target}. "
    "Use natural, contextual translation appropriate for a software UI — "
    "do NOT translate literally. Adapt idioms and expressions to sound natural "
    "in the target language. "
    "IMPORTANT: Preserve any XML tags like <x1/>, <x2/>, placeholders like "
    "{{}}, %s, %d, and formatting codes exactly as they are. "
    "Return ONLY the translated text, nothing else."
)


def build_translation_prompt(
    source: str,
    target: str,
    app_name: str = "",
    context_entries: Optional[list[str]] = None,
) -> str:
    """Build the translation system prompt with application context.

    Args:
        source: Source language name or code.
        target: Target language name or code.
        app_name: Application textdomain / identifier (e.g. 'ashy-term').
        context_entries: Sample msgid strings for disambiguation.
    """
    # Derive a human-readable display name from the textdomain
    display_name = (
        app_name.replace("-", " ").replace("_", " ").title() if app_name else "unknown"
    )

    context_section = ""
    if context_entries:
        samples = "\n".join(f"  - {entry}" for entry in context_entries[:15])
        context_section = (
            f"\nFor context, other UI strings from this application include:\n"
            f"{samples}\n"
        )

    return _TRANSLATION_PROMPT.format(
        source=_resolve_lang(source),
        target=_resolve_lang(target),
        app_name=display_name,
        context_section=context_section,
    )


_BATCH_PROMPT = (
    "You are a professional translator specializing in software localization. "
    "You are translating UI strings for the application '{app_name}'. "
    "Translate ALL the following texts from {source} to {target}.\n\n"
    "CRITICAL RULES:\n"
    "1. NEVER translate the application name '{app_name}' — it is a proper noun.\n"
    "2. NEVER translate brand names, product names, or proper nouns.\n"
    "3. Use natural, contextual translation for a software UI.\n"
    "4. Preserve XML tags (<x1/>, <x2/>), placeholders ({{}}, %s, %d) exactly.\n"
    "5. Return ONLY the translated texts, one per line, in the EXACT same order.\n"
    "6. Use the separator |||NEXT||| between each translation.\n"
    "7. Do NOT add numbering, bullet points, or any extra text.\n"
    "{context_section}"
)


def build_batch_prompt(
    source: str,
    target: str,
    app_name: str = "",
    context_entries: Optional[list[str]] = None,
) -> str:
    """Build a batch translation system prompt."""
    display_name = (
        app_name.replace("-", " ").replace("_", " ").title() if app_name else "unknown"
    )
    context_section = ""
    if context_entries:
        samples = "\n".join(f"  - {entry}" for entry in context_entries[:15])
        context_section = (
            f"\nFor context, other UI strings from this application include:\n"
            f"{samples}\n"
        )
    return _BATCH_PROMPT.format(
        source=_resolve_lang(source),
        target=_resolve_lang(target),
        app_name=display_name,
        context_section=context_section,
    )


def clean_batch_parts(raw: str) -> list[str]:
    """Split batch response and strip empty parts from leading/trailing separators.

    LLMs sometimes return '|||NEXT|||Trans1|||NEXT|||Trans2' which produces
    an empty first element after split, shifting all translations by one.
    """
    parts = [p.strip() for p in raw.split("|||NEXT|||")]
    while parts and not parts[0]:
        parts.pop(0)
    while parts and not parts[-1]:
        parts.pop()
    return parts


class TranslationAPI(ABC):
    """Classe base para todas as APIs de tradução."""

    # Seconds to wait between outer batch calls in translate_language().
    # Override in subclasses with strict RPM limits (e.g. Gemini).
    batch_delay: float = 0.0

    # Cost per million tokens (USD). Override in paid subclasses.
    # Format: (input_cost_per_1M, output_cost_per_1M)
    _token_pricing: tuple[float, float] = (0.0, 0.0)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def _reset_usage(self) -> None:
        """Reset accumulated token/cost counters."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost_usd = 0.0
        self._api_calls = 0

    def _track_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Accumulate token usage and compute cost."""
        if not hasattr(self, "_total_input_tokens"):
            self._reset_usage()
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._api_calls += 1
        inp_price, out_price = self._token_pricing
        self._total_cost_usd += (
            input_tokens * inp_price / 1_000_000
            + output_tokens * out_price / 1_000_000
        )

    def get_usage(self) -> dict:
        """Return accumulated usage statistics.

        Returns dict with: input_tokens, output_tokens, total_tokens,
        cost_usd, api_calls.
        """
        if not hasattr(self, "_total_input_tokens"):
            self._reset_usage()
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total_tokens": self._total_input_tokens + self._total_output_tokens,
            "cost_usd": round(self._total_cost_usd, 6),
            "api_calls": self._api_calls,
        }

    def set_context(
        self, app_name: str, context_entries: Optional[list[str]] = None
    ) -> None:
        """Set translation context (app name and sample strings).

        Called by TranslationEngine before translating a language.
        LLM-based APIs use this to enrich the system prompt.
        """
        self._app_name = app_name
        self._context_entries = context_entries or []

    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Traduz texto entre idiomas.

        Args:
            text: Texto a ser traduzido
            source_lang: Código do idioma de origem (ex: 'en')
            target_lang: Código do idioma de destino (ex: 'pt-BR')

        Returns:
            Texto traduzido
        """
        pass

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Translate multiple texts in a single LLM call (when supported).

        Default implementation falls back to individual calls.
        LLM-based subclasses override this for efficiency.
        """
        return [self.translate(t, source_lang, target_lang) for t in texts]

    @abstractmethod
    def test_connection(self) -> bool:
        """
        Testa se a API está acessível e funcionando.

        Returns:
            True se conectado com sucesso, False caso contrário
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Retorna o nome da API."""
        pass


def _parse_retry_delay(error_msg: str) -> float | None:
    """Extract retryDelay from Gemini/Google API error messages."""
    import re

    match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r"retryDelay.*?(\d+)s", error_msg)
    if match:
        return float(match.group(1))
    return None


def retry_on_rate_limit(func):
    """Decorator that retries API calls on HTTP 429 / rate limit errors.

    Respects Retry-After header and Gemini's retryDelay field.
    """
    import functools
    import requests

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        backoff = _INITIAL_BACKOFF
        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, "status_code", 0)
                if status == 429 and attempt < _MAX_RETRIES - 1:
                    retry_after = e.response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else backoff
                    log.warning(
                        "Rate limited (429). Retrying in %.1fs (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    backoff *= _BACKOFF_FACTOR
                    continue
                raise
            except Exception as e:
                msg = str(e).lower()
                is_quota = "quota" in msg or "resource_exhausted" in msg
                is_rate = "rate" in msg or "429" in msg
                if (is_quota or is_rate) and attempt < _MAX_RETRIES - 1:
                    # Quota exhaustion is persistent — fail fast after 1 retry
                    if is_quota and attempt >= 1:
                        log.error("Quota exhausted — aborting retries.")
                        raise
                    # Try to extract retry delay from error message
                    delay = _parse_retry_delay(str(e))
                    wait = delay if delay else backoff
                    log.warning(
                        "Rate limit detected. Retrying in %.1fs (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    backoff *= _BACKOFF_FACTOR
                    continue
                raise
        return func(*args, **kwargs)

    return wrapper
