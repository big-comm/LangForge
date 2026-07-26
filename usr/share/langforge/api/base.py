"""Interface abstrata para APIs de tradução."""

import functools
import json
import logging
import math
import random
import re
import secrets
import time
from abc import ABC, abstractmethod
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

from core.languages import SUPPORTED_LANGUAGES

log = logging.getLogger(__name__)

# Retry settings for rate-limited requests
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 2.0  # seconds
_BACKOFF_FACTOR = 2.0
_MAX_RETRY_DELAY = 60.0
_RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429})
_STATUS_NAME_TO_HTTP = {
    "ABORTED": 409,
    "ALREADY_EXISTS": 409,
    "CANCELLED": 499,
    "DEADLINE_EXCEEDED": 408,
    "INTERNAL": 500,
    "INVALID_ARGUMENT": 400,
    "NOT_FOUND": 404,
    "PERMISSION_DENIED": 403,
    "RESOURCE_EXHAUSTED": 429,
    "UNAUTHENTICATED": 401,
    "UNAVAILABLE": 503,
}
_NETWORK_EXCEPTION_NAMES = {
    "aiohttp": frozenset(
        {
            "ClientConnectionError",
            "ServerConnectionError",
            "ServerTimeoutError",
        }
    ),
    "httpcore": frozenset({"NetworkError", "TimeoutException"}),
    "httpx": frozenset({"NetworkError", "TimeoutException"}),
    "openai": frozenset({"APIConnectionError", "APITimeoutError"}),
}

# Shared prompt template for all LLM-based translation APIs.
# Placeholders: {source}, {target}, {app_name}, {context_section}


def _resolve_lang(code: str) -> str:
    """Resolve a language code to its full name for clearer LLM prompts."""
    return SUPPORTED_LANGUAGES.get(code, code)


_TRANSLATION_PROMPT = (
    "You are a professional translator specializing in software localization. "
    "The project's gettext textdomain is '{app_name}'. "
    "Translate the following text from {source} to {target}.\n\n"
    "CRITICAL RULES:\n"
    "1. Use the textdomain only as project context. Preserve it when it appears as "
    "a technical identifier or proper project name, but translate ordinary words "
    "normally; a textdomain is not automatically a display name.\n"
    "2. NEVER translate brand names, product names, project names, or proper nouns.\n"
    "3. Use natural, contextual translation appropriate for a software UI — "
    "do NOT translate word-by-word or literally.\n"
    "4. Adapt idioms and expressions to sound natural in the target language.\n"
    "5. Preserve any XML tags like <x1/>, <x2/>, placeholders like "
    "{{}}, %s, %d, and formatting codes EXACTLY as they are. "
    "Do NOT translate, rename, or modify ANY text inside curly braces {{}} or XML tags. "
    "For example, {{total}} must stay as {{total}}, NOT be translated.\n"
    "6. Preserve commands, paths, URLs, references, flags, environment variables, "
    "and software identifiers byte-for-byte.\n"
    "7. Preserve the exact newline count and leading/trailing whitespace.\n"
    "8. Preserve numeric values, signs, percentages, order, negation, tense, and "
    "the role of every placeholder. Decimal and thousands separators may follow "
    "the target locale. "
    "Silently verify these constraints before answering.\n"
    "9. Return ONLY the translated text. Never emit protocol markers, labels, "
    "explanations, or Markdown fences.\n"
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
    item_instruction: str = "",
) -> str:
    """Build the translation system prompt with application context.

    Args:
        source: Source language name or code.
        target: Target language name or code.
        app_name: Application textdomain / identifier (e.g. 'ashy-term').
        context_entries: Sample msgid strings for disambiguation.
        item_instruction: Trusted per-item grammatical constraint.
    """
    display_name = app_name or "unknown"

    context_section = ""
    if context_entries:
        samples = "\n".join(f"  - {entry}" for entry in context_entries[:15])
        context_section = (
            f"\nFor context, other UI strings from this application include:\n"
            f"{samples}\n"
        )
    if item_instruction:
        context_section += (
            f"\nAdditional requirement for this item:\n  - {item_instruction}\n"
        )

    return _TRANSLATION_PROMPT.format(
        source=_resolve_lang(source),
        target=_resolve_lang(target),
        app_name=display_name,
        context_section=context_section,
    )


_BATCH_PROMPT = (
    "You are a professional translator specializing in software localization. "
    "The project's gettext textdomain is '{app_name}'. "
    "Translate ALL the following texts from {source} to {target}.\n\n"
    "CRITICAL RULES:\n"
    "1. Use the textdomain only as project context. Preserve it when it appears as "
    "a technical identifier or proper project name, but translate ordinary words "
    "normally.\n"
    "2. NEVER translate brand names, product names, or proper nouns.\n"
    "3. Use natural, contextual translation for a software UI.\n"
    "4. Preserve XML tags (<x1/>, <x2/>), placeholders ({{}}, %s, %d), commands, "
    "paths, URLs, references, flags, environment variables, and software identifiers "
    "EXACTLY as-is.\n"
    "5. Preserve exact newlines, leading/trailing whitespace, numeric values, signs, "
    "percentages, order, negation, tense, and the role of every placeholder. Decimal "
    "and thousands separators may follow the target locale.\n"
    "6. The user message is a JSON array of objects with 'id' and 'text'. Return a JSON "
    "array of objects containing exactly 'id' and 'translation'. Copy every id "
    "byte-for-byte. Include every id exactly once; response order does not matter.\n"
    "7. Return JSON only. Never add Markdown fences, explanations, labels, protocol "
    "markers, or extra keys. Silently verify the complete response before answering.\n"
    "{context_section}"
)


def build_batch_prompt(
    source: str,
    target: str,
    app_name: str = "",
    context_entries: Optional[list[str]] = None,
) -> str:
    """Build a batch translation system prompt."""
    display_name = app_name or "unknown"
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


class BatchAlignmentError(ValueError):
    """Raised when a batch response cannot be aligned safely."""


def prepare_batch_request(
    texts: list[str],
    request_id: Optional[str] = None,
) -> tuple[str, list[str]]:
    """Build a JSON batch with request-scoped opaque item IDs."""
    if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
        raise TypeError("Batch texts must be a list of strings")
    if request_id is None:
        request_id = secrets.token_hex(12)
    if not isinstance(request_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{8,64}", request_id
    ):
        raise ValueError("Invalid batch request ID")

    item_ids = [f"lf-{request_id}-{index}" for index in range(1, len(texts) + 1)]
    payload = [{"id": item_id, "text": text} for item_id, text in zip(item_ids, texts)]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), item_ids


def parse_batch_response(raw: str, expected_ids: list[str]) -> list[str]:
    """Parse a strict JSON response and align translations by opaque ID."""
    if not isinstance(raw, str):
        raise BatchAlignmentError("Batch response must be text")
    if not isinstance(expected_ids, list) or any(
        not isinstance(item_id, str) or not item_id for item_id in expected_ids
    ):
        raise ValueError("expected_ids must be a list of non-empty strings")
    if len(set(expected_ids)) != len(expected_ids):
        raise ValueError("expected_ids contains duplicates")

    def strict_object(pairs):
        parsed = {}
        for key, value in pairs:
            if key in parsed:
                raise BatchAlignmentError(
                    f"Batch response contains duplicate key {key!r}"
                )
            parsed[key] = value
        return parsed

    try:
        response = json.loads(raw, object_pairs_hook=strict_object)
    except json.JSONDecodeError as error:
        raise BatchAlignmentError("Batch response is not valid JSON") from error
    if not isinstance(response, list):
        raise BatchAlignmentError("Batch response must be a JSON array")
    if len(response) != len(expected_ids):
        raise BatchAlignmentError(
            "Batch response cardinality mismatch: "
            f"expected {len(expected_ids)}, got {len(response)}"
        )

    expected = set(expected_ids)
    by_id: dict[str, str] = {}
    for index, item in enumerate(response):
        if not isinstance(item, dict) or set(item) != {"id", "translation"}:
            raise BatchAlignmentError(
                f"Batch response item {index + 1} has an invalid schema"
            )
        item_id = item["id"]
        translation = item["translation"]
        if not isinstance(item_id, str) or not isinstance(translation, str):
            raise BatchAlignmentError(
                f"Batch response item {index + 1} must contain strings"
            )
        if item_id not in expected:
            raise BatchAlignmentError(f"Batch response contains unknown ID {item_id!r}")
        if item_id in by_id:
            raise BatchAlignmentError(
                f"Batch response contains duplicate ID {item_id!r}"
            )
        if not translation:
            raise BatchAlignmentError(
                f"Batch response contains an empty translation for {item_id!r}"
            )
        by_id[item_id] = translation

    missing = [item_id for item_id in expected_ids if item_id not in by_id]
    if missing:
        raise BatchAlignmentError(
            f"Batch response is missing IDs: {', '.join(missing)}"
        )
    return [by_id[item_id] for item_id in expected_ids]


class TranslationAPI(ABC):
    """Classe base para todas as APIs de tradução."""

    # Seconds to wait between outer batch calls in translate_language().
    # Override in subclasses with strict RPM limits (e.g. Gemini).
    batch_delay: float = 0.0
    supports_item_instructions: bool = False
    supports_context: bool = False

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
            input_tokens * inp_price / 1_000_000 + output_tokens * out_price / 1_000_000
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

    def translate_with_instruction(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        instruction: str,
    ) -> str:
        """Translate one item with a trusted temporary system instruction."""
        if not self.supports_item_instructions:
            raise NotImplementedError(
                f"{type(self).__name__} does not support item instructions"
            )
        sentinel = object()
        previous = getattr(self, "_item_instruction", sentinel)
        self._item_instruction = instruction
        try:
            return self.translate(text, source_lang, target_lang)
        finally:
            if previous is sentinel:
                del self._item_instruction
            else:
                self._item_instruction = previous

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
    patterns = (
        r"\bretry\s+in\s+(\d+(?:\.\d+)?)\s*s\b",
        r"\bretryDelay\b.*?(\d+(?:\.\d+)?)\s*s\b",
    )
    for pattern in patterns:
        match = re.search(pattern, error_msg, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _coerce_status_code(value) -> int | None:
    """Convert SDK status representations to an HTTP status code."""
    if value is None or isinstance(value, bool):
        return None
    if callable(value):
        try:
            value = value()
        except TypeError:
            return None

    name = getattr(value, "name", None)
    if isinstance(name, str) and name.upper() in _STATUS_NAME_TO_HTTP:
        return _STATUS_NAME_TO_HTTP[name.upper()]

    if isinstance(value, str):
        status_name = value.strip().rsplit(".", 1)[-1].upper()
        if status_name in _STATUS_NAME_TO_HTTP:
            return _STATUS_NAME_TO_HTTP[status_name]
        value = value.strip()
    else:
        value = getattr(value, "value", value)

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None

    status = int(numeric)
    return status if 100 <= status <= 599 else None


def _exception_status_code(error: Exception) -> int | None:
    """Read a status code from requests and common API SDK exceptions."""
    response = getattr(error, "response", None)
    candidates = (
        getattr(error, "status_code", None),
        getattr(response, "status_code", None),
        getattr(error, "code", None),
        getattr(error, "status", None),
        getattr(error, "grpc_status_code", None),
    )
    for candidate in candidates:
        status = _coerce_status_code(candidate)
        if status is not None:
            return status
    return None


def _is_connection_or_timeout(error: Exception) -> bool:
    """Identify concrete network exceptions, including wrapped SDK errors."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(
            current,
            (
                ConnectionError,
                TimeoutError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ),
        ):
            return True

        for exception_type in type(current).__mro__:
            module = exception_type.__module__.partition(".")[0]
            names = _NETWORK_EXCEPTION_NAMES.get(module)
            if names and exception_type.__name__ in names:
                return True

        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def _is_retryable_error(error: Exception) -> bool:
    """Classify transient failures without inspecting human-readable text."""
    status = _exception_status_code(error)
    if status is not None:
        return status in _RETRYABLE_HTTP_STATUSES or 500 <= status <= 599
    return _is_connection_or_timeout(error)


def _parse_numeric_delay(value) -> float | None:
    """Parse and safely bound a numeric server-provided delay."""
    if value is None or isinstance(value, bool):
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(delay) or delay < 0:
        return None
    return min(delay, _MAX_RETRY_DELAY)


def _retry_after_delay(error: Exception) -> float | None:
    """Read a bounded delta-seconds or HTTP-date Retry-After header."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        retry_after = headers.get("Retry-After")
    except AttributeError:
        return None
    numeric_delay = _parse_numeric_delay(retry_after)
    if numeric_delay is not None:
        return numeric_delay
    if not isinstance(retry_after, str):
        return None

    try:
        retry_at = parsedate_to_datetime(retry_after)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        delay = retry_at.timestamp() - time.time()
    except (OverflowError, TypeError, ValueError):
        return None
    return min(max(delay, 0.0), _MAX_RETRY_DELAY)


def _retry_wait(error: Exception, failure_index: int) -> float:
    """Choose server delay or bounded exponential backoff with equal jitter."""
    server_delay = _retry_after_delay(error)
    if server_delay is not None:
        return server_delay

    gemini_delay = _parse_retry_delay(str(error))
    if gemini_delay is not None:
        return min(gemini_delay, _MAX_RETRY_DELAY)

    backoff = min(
        _INITIAL_BACKOFF * (_BACKOFF_FACTOR**failure_index),
        _MAX_RETRY_DELAY,
    )
    return random.uniform(backoff / 2, backoff)


def retry_on_rate_limit(func):
    """Retry transient API errors up to ``_MAX_RETRIES`` total calls.

    Respects Retry-After header and Gemini's retryDelay field.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as error:
                if not _is_retryable_error(error) or attempt == _MAX_RETRIES - 1:
                    raise

                wait = _retry_wait(error, attempt)
                status = _exception_status_code(error)
                reason = f"HTTP {status}" if status else type(error).__name__
                log.warning(
                    "Transient API error (%s). Retrying in %.1fs (attempt %d/%d)",
                    reason,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(wait)
        raise RuntimeError("Retry loop exhausted without an exception")

    return wrapper
