"""Implementações de APIs de tradução com tier gratuito."""

import logging

import requests

from api.base import (
    TranslationAPI,
    build_batch_prompt,
    build_translation_prompt,
    parse_batch_response,
    prepare_batch_request,
    retry_on_rate_limit,
)
from api.models import ModelSpec, default_model, get_model, normalize_model
from core.languages import get_api_lang_code

log = logging.getLogger(__name__)

# Maximum strings per batch call
_BATCH_SIZE = 15


def _value(obj, name: str, default=0):
    """Read one field from SDK objects or dictionaries."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(name, default)
    else:
        value = getattr(obj, name, default)
    return default if value is None else value


def _track_model_usage(
    api: TranslationAPI,
    spec: ModelSpec | None,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> None:
    """Track free-tier token usage without reporting hypothetical charges."""
    if not hasattr(api, "_total_input_tokens"):
        api._reset_usage()
    api._total_input_tokens += input_tokens
    api._total_output_tokens += output_tokens
    api._api_calls += 1


class GroqAPI(TranslationAPI):
    """Groq GPT-OSS translation through the OpenAI-compatible endpoint."""

    supports_item_instructions = True
    supports_context = True
    batch_delay = 2.0  # Groq free: 30 RPM

    def __init__(
        self,
        api_key: str,
        model: str = default_model("groq"),
    ):
        self.api_key = api_key
        self.model = normalize_model("groq", model)
        self._model_spec = get_model("groq", self.model)
        if self._model_spec:
            self._token_pricing = self._model_spec.token_pricing
        self._reset_usage()
        self.session = requests.Session()
        self.base_url = "https://api.groq.com/openai/v1"

    def _payload(self, messages: list[dict], max_tokens: int) -> dict:
        """Build the supported GPT-OSS Chat Completions payload."""
        return {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": "low",
            "include_reasoning": False,
        }

    def _track_groq_response(self, data: dict) -> None:
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        _track_model_usage(
            self,
            self._model_spec,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            details.get("cached_tokens", 0),
        )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Groq."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
            getattr(self, "_item_instruction", ""),
        )
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=self._payload(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                512,
            ),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self._track_groq_response(data)
        return data["choices"][0]["message"]["content"].strip()

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Translate multiple texts using Groq with sub-batches of 8.

        Smaller sub-batches reduce JSON schema drift and rate limit hits.
        """
        import time as _time

        sub_batch_size = 8
        results: list[str] = []
        for start in range(0, len(texts), sub_batch_size):
            if start > 0:
                _time.sleep(self.batch_delay)
            chunk = texts[start : start + sub_batch_size]
            results.extend(self._do_batch(chunk, source_lang, target_lang))
        return results

    @retry_on_rate_limit
    def _do_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        system_prompt = build_batch_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
        )
        user_msg, expected_ids = prepare_batch_request(texts)
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=self._payload(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                4096,
            ),
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        self._track_groq_response(data)
        content = data["choices"][0]["message"]["content"].strip()
        return parse_batch_response(content, expected_ids, texts)

    def test_connection(self) -> bool:
        """Testa conexão com Groq."""
        try:
            response = self.session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            raise ConnectionError(f"Groq: {e}") from e

    def get_name(self) -> str:
        return f"Groq ({self.model})"


class LibreTranslateAPI(TranslationAPI):
    """LibreTranslate public or self-hosted translation API."""

    def __init__(
        self,
        url: str = "https://libretranslate.com",
        api_key: str = "",
    ):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando LibreTranslate."""
        source = get_api_lang_code(source_lang)
        target = get_api_lang_code(target_lang)

        payload = {
            "q": text,
            "source": source,
            "target": target,
            "format": "text",
        }
        if self.api_key:
            payload["api_key"] = self.api_key
        response = self.session.post(
            f"{self.url}/translate",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["translatedText"]

    def test_connection(self) -> bool:
        """Testa conexão com LibreTranslate."""
        try:
            return bool(self.translate("test", "en", "es"))
        except Exception as e:
            raise ConnectionError(f"LibreTranslate: {e}") from e

    def get_name(self) -> str:
        return "LibreTranslate"


class DeepLFreeAPI(TranslationAPI):
    """DeepL API with automatic Free/Pro endpoint selection."""

    # Supported LangForge targets and their DeepL API codes.
    DEEPL_LANG_MAP = {
        "bg": "BG",
        "cs": "CS",
        "da": "DA",
        "de": "DE",
        "el": "EL",
        "en": "EN-US",
        "es": "ES",
        "et": "ET",
        "fi": "FI",
        "fr": "FR",
        "he": "HE",
        "hu": "HU",
        "it": "IT",
        "ja": "JA",
        "ko": "KO",
        "nl": "NL",
        "no": "NB",  # Norwegian Bokmål
        "pl": "PL",
        "pt-BR": "PT-BR",
        "pt": "PT-PT",
        "ro": "RO",
        "ru": "RU",
        "sk": "SK",
        "sv": "SV",
        "tr": "TR",
        "uk": "UK",
        "zh": "ZH",
        # Not supported by DeepL: hr (Croatian), is (Icelandic)
    }

    def __init__(self, api_key: str):
        import time

        self.api_key = api_key
        self.session = requests.Session()
        # Keys ending in ':fx' use the free API, others use the pro API
        if api_key.strip().endswith(":fx"):
            self.base_url = "https://api-free.deepl.com/v2"
        else:
            self.base_url = "https://api.deepl.com/v2"
        self._time = time
        self._last_request: float = 0.0
        self._resolved = False

    def _ensure_endpoint(self) -> None:
        """Try current endpoint; on 403 swap to the other one."""
        if self._resolved:
            return
        response = self.session.get(
            f"{self.base_url}/usage",
            headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
            timeout=10,
        )
        if response.status_code != 403:
            response.raise_for_status()
            self._resolved = True
            return
        # Swap endpoint and retry
        if "api-free" in self.base_url:
            self.base_url = "https://api.deepl.com/v2"
        else:
            self.base_url = "https://api-free.deepl.com/v2"
        self._resolved = True

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text using DeepL."""
        self._ensure_endpoint()
        target = self.DEEPL_LANG_MAP.get(target_lang)
        if not target:
            raise ValueError(f"Language '{target_lang}' is not supported by DeepL")

        # Rate limiting: max 5 requests/second
        elapsed = self._time.time() - self._last_request
        if elapsed < 0.2:
            self._time.sleep(0.2 - elapsed)

        response = self.session.post(
            f"{self.base_url}/translate",
            headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
            data={
                "text": text,
                "source_lang": "EN",
                "target_lang": target,
                "tag_handling": "xml",
                "ignore_tags": "x",
            },
            timeout=30,
        )
        self._last_request = self._time.time()

        if response.status_code == 456:
            raise RuntimeError("DeepL quota exceeded (500k chars/month)")
        response.raise_for_status()
        return response.json()["translations"][0]["text"]

    def test_connection(self) -> bool:
        """Test connection with DeepL."""
        self._ensure_endpoint()
        try:
            response = self.session.get(
                f"{self.base_url}/usage",
                headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                timeout=10,
            )
            if response.status_code == 403:
                raise ConnectionError("DeepL: Invalid API key")
            response.raise_for_status()
            return True
        except ConnectionError:
            raise
        except Exception as e:
            raise ConnectionError(f"DeepL: {e}") from e

    def get_usage(self) -> dict:
        """Fetch DeepL usage statistics.

        Returns dict with 'character_count' and 'character_limit'.
        """
        self._ensure_endpoint()
        response = self.session.get(
            f"{self.base_url}/usage",
            headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_name(self) -> str:
        return "DeepL Free (500k chars/month)"


class GeminiFreeAPI(TranslationAPI):
    """Gemini Flash translation through the Google Gen AI free tier."""

    supports_item_instructions = True
    supports_context = True
    batch_delay = 5.0

    def __init__(
        self,
        api_key: str,
        model: str = default_model("gemini-free"),
    ):
        try:
            from google import genai
        except ImportError:
            raise ImportError("Install: pip install google-genai")

        self.model_name = normalize_model("gemini-free", model)
        self._model_spec = get_model("gemini-free", self.model_name)
        self.client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 60_000},
        )
        self._reset_usage()

    def _track_gemini_response(self, response) -> None:
        meta = getattr(response, "usage_metadata", None)
        if meta:
            _track_model_usage(
                self,
                self._model_spec,
                _value(meta, "prompt_token_count"),
                _value(meta, "candidates_token_count"),
                _value(meta, "cached_content_token_count"),
            )

    def _config(self, max_output_tokens: int, system_instruction: str = "") -> dict:
        """Build Gemini 3 generation config without deprecated sampling fields."""
        config: dict = {"max_output_tokens": max_output_tokens}
        if system_instruction:
            config["system_instruction"] = system_instruction
        profile = self._model_spec.request_profile if self._model_spec else ""
        if profile == "gemini-3-flash-lite":
            config["thinking_config"] = {"thinking_level": "minimal"}
        elif profile == "gemini-3-flash":
            config["thinking_config"] = {"thinking_level": "minimal"}
        else:
            config["temperature"] = 0.3
        return config

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Gemini."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
            getattr(self, "_item_instruction", ""),
        )
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=text,
            config=self._config(512, system_prompt),
        )
        self._track_gemini_response(response)
        return response.text.strip()

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Gemini Free has strict rate limits (15 RPM) — use sub-batches."""
        import time as _time

        sub_batch_size = 15
        results: list[str] = []
        for start in range(0, len(texts), sub_batch_size):
            if start > 0:
                _time.sleep(self.batch_delay)
            chunk = texts[start : start + sub_batch_size]
            results.extend(self._do_batch(chunk, source_lang, target_lang))
        return results

    @retry_on_rate_limit
    def _do_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        system_prompt = build_batch_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
        )
        user_msg, expected_ids = prepare_batch_request(texts)
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_msg,
            config=self._config(4096, system_prompt),
        )
        self._track_gemini_response(response)
        return parse_batch_response(response.text, expected_ids, texts)

    def test_connection(self) -> bool:
        """Test Gemini connection with actual generation."""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents="Say OK",
                config=self._config(10),
            )
            return bool(response.text)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                raise ConnectionError(
                    "Gemini: quota exceeded. "
                    "Wait for the daily reset or check your AI Studio limits."
                ) from e
            if "401" in msg or "403" in msg or "API_KEY_INVALID" in msg:
                raise ConnectionError("Gemini: invalid API key.") from e
            raise ConnectionError(f"Gemini: {e}") from e

    def get_name(self) -> str:
        return f"Gemini Free ({self.model_name})"


class OpenRouterAPI(TranslationAPI):
    """OpenRouter's availability-aware free model router."""

    supports_item_instructions = True
    supports_context = True
    batch_delay = 2.0  # Conservative for varied free model limits

    def __init__(
        self,
        api_key: str,
        model: str = default_model("openrouter"),
    ):
        self.api_key = api_key
        self.model = normalize_model("openrouter", model)
        self._model_spec = get_model("openrouter", self.model)
        self._reset_usage()
        self.session = requests.Session()
        self.base_url = "https://openrouter.ai/api/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/big-comm/LangForge",
            "X-Title": "LangForge",
        }

    def _payload(self, messages: list[dict], max_tokens: int) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        if (
            self._model_spec
            and self._model_spec.request_profile == "openrouter-gpt-oss"
        ):
            payload["reasoning"] = {"effort": "low", "exclude": True}
        return payload

    def _track_openrouter_response(self, data: dict) -> None:
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        _track_model_usage(
            self,
            self._model_spec,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            details.get("cached_tokens", 0),
        )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando OpenRouter."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
            getattr(self, "_item_instruction", ""),
        )
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=self._payload(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                512,
            ),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self._track_openrouter_response(data)
        return data["choices"][0]["message"]["content"].strip()

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Sub-batch of 8 for free models (varied RPM limits)."""
        import time as _time

        sub_batch_size = 8
        results: list[str] = []
        for start in range(0, len(texts), sub_batch_size):
            if start > 0:
                _time.sleep(self.batch_delay)
            chunk = texts[start : start + sub_batch_size]
            results.extend(self._do_batch(chunk, source_lang, target_lang))
        return results

    @retry_on_rate_limit
    def _do_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        system_prompt = build_batch_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
        )
        user_msg, expected_ids = prepare_batch_request(texts)
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=self._payload(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                4096,
            ),
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        self._track_openrouter_response(data)
        content = data["choices"][0]["message"]["content"].strip()
        return parse_batch_response(content, expected_ids, texts)

    def test_connection(self) -> bool:
        """Testa conexão com OpenRouter."""
        try:
            response = self.session.get(
                f"{self.base_url}/key",
                headers=self._headers(),
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            raise ConnectionError(f"OpenRouter: {e}") from e

    def get_name(self) -> str:
        return f"OpenRouter ({self.model})"


class MistralFreeAPI(TranslationAPI):
    """Current Mistral generalist models on the Experiment tier."""

    supports_item_instructions = True
    supports_context = True
    batch_delay = 2.0  # Conservative for free tier

    def __init__(
        self,
        api_key: str,
        model: str = default_model("mistral-free"),
    ):
        self.api_key = api_key
        self.model = normalize_model("mistral-free", model)
        self._model_spec = get_model("mistral-free", self.model)
        if self._model_spec:
            self._token_pricing = self._model_spec.token_pricing
        self._reset_usage()
        self.session = requests.Session()
        self.base_url = "https://api.mistral.ai/v1"

    def _payload(self, messages: list[dict], max_tokens: int) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        if (
            self._model_spec
            and self._model_spec.request_profile == "mistral-reasoning-none"
        ):
            payload["reasoning_effort"] = "none"
        return payload

    def _track_mistral_response(self, data: dict) -> None:
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        _track_model_usage(
            self,
            self._model_spec,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            details.get("cached_tokens", 0),
        )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Mistral."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
            getattr(self, "_item_instruction", ""),
        )
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=self._payload(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                512,
            ),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self._track_mistral_response(data)
        return data["choices"][0]["message"]["content"].strip()

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Sub-batch of 10 for Mistral free tier."""
        import time as _time

        sub_batch_size = 10
        results: list[str] = []
        for start in range(0, len(texts), sub_batch_size):
            if start > 0:
                _time.sleep(self.batch_delay)
            chunk = texts[start : start + sub_batch_size]
            results.extend(self._do_batch(chunk, source_lang, target_lang))
        return results

    @retry_on_rate_limit
    def _do_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        system_prompt = build_batch_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
        )
        user_msg, expected_ids = prepare_batch_request(texts)
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=self._payload(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                4096,
            ),
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        self._track_mistral_response(data)
        content = data["choices"][0]["message"]["content"].strip()
        return parse_batch_response(content, expected_ids, texts)

    def test_connection(self) -> bool:
        """Testa conexão com Mistral."""
        try:
            response = self.session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            raise ConnectionError(f"Mistral: {e}") from e

    def get_name(self) -> str:
        return f"Mistral Free ({self.model})"
