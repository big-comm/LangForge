"""Implementações de APIs de tradução pagas."""

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

log = logging.getLogger(__name__)

# Maximum strings per batch call (keep token usage under control)
_BATCH_SIZE = 15


def _value(obj, name: str, default=0):
    """Read one field from SDK objects or plain dictionaries."""
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
    """Track tokens with model-specific cached pricing."""
    if not hasattr(api, "_total_input_tokens"):
        api._reset_usage()
    api._total_input_tokens += input_tokens
    api._total_output_tokens += output_tokens
    api._api_calls += 1
    if spec:
        api._total_cost_usd += spec.estimate_cost(
            input_tokens,
            output_tokens,
            cached_input_tokens,
        )


class OpenAIAPI(TranslationAPI):
    """OpenAI GPT-5 translation through Chat Completions."""

    supports_item_instructions = True
    supports_context = True

    def __init__(
        self,
        api_key: str,
        model: str = default_model("openai"),
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai: pip install openai")

        self.client = OpenAI(api_key=api_key, timeout=60.0, max_retries=0)
        self.model = normalize_model("openai", model)
        self._model_spec = get_model("openai", self.model)
        if self._model_spec:
            self._token_pricing = self._model_spec.token_pricing
        self._reset_usage()

    def _instruction_role(self) -> str:
        return "developer" if self._model_spec else "system"

    def _completion_options(self, max_tokens: int) -> dict:
        if (
            self._model_spec
            and self._model_spec.request_profile == "openai-no-reasoning"
        ):
            return {
                "reasoning_effort": "none",
                "max_completion_tokens": max_tokens,
            }
        return {"temperature": 0.3, "max_tokens": max_tokens}

    def _track_openai_response(self, response) -> None:
        """Extract and track token usage from an OpenAI response."""
        usage = getattr(response, "usage", None)
        if usage:
            details = _value(usage, "prompt_tokens_details", None)
            _track_model_usage(
                self,
                self._model_spec,
                _value(usage, "prompt_tokens"),
                _value(usage, "completion_tokens"),
                _value(details, "cached_tokens"),
            )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando OpenAI."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
            getattr(self, "_item_instruction", ""),
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": self._instruction_role(), "content": system_prompt},
                {"role": "user", "content": text},
            ],
            **self._completion_options(512),
        )
        self._track_openai_response(response)
        content = response.choices[0].message.content
        return content.strip() if content else ""

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Translate multiple texts using OpenAI with sub-batches of 15."""
        sub_batch_size = 15
        results: list[str] = []
        for start in range(0, len(texts), sub_batch_size):
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
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": self._instruction_role(), "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            **self._completion_options(4096),
        )
        self._track_openai_response(response)
        content = response.choices[0].message.content or ""
        return parse_batch_response(content, expected_ids, texts)

    def test_connection(self) -> bool:
        """Testa conexão com OpenAI."""
        try:
            # List models is the simplest way to verify API key validity
            models = self.client.models.list()
            # Consume at least one result to confirm access
            next(iter(models))
            return True
        except Exception as e:
            log.debug("OpenAI test error: %s", e)
            raise ConnectionError(f"OpenAI: {e}") from e

    def get_name(self) -> str:
        return f"OpenAI ({self.model})"


class GeminiAPI(TranslationAPI):
    """
    API paga do Google Gemini.
    Uses new google-genai SDK (replaces deprecated google-generativeai).
    """

    supports_item_instructions = True
    supports_context = True
    batch_delay = 0.1  # Paid tier has 2000 RPM; retry handles bursts

    def __init__(
        self,
        api_key: str,
        model: str = default_model("gemini"),
    ):
        try:
            from google import genai
        except ImportError:
            raise ImportError("Install: pip install google-genai")

        self.model_name = normalize_model("gemini", model)
        self._model_spec = get_model("gemini", self.model_name)
        if self._model_spec:
            self._token_pricing = self._model_spec.token_pricing
        self.client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 60_000},
        )
        self._reset_usage()

    def _config(self, max_output_tokens: int, system_instruction: str = "") -> dict:
        """Build Gemini 3 config without deprecated sampling parameters."""
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

    def _track_gemini_response(self, response) -> None:
        """Extract and track token usage from a Gemini response."""
        meta = getattr(response, "usage_metadata", None)
        if meta:
            _track_model_usage(
                self,
                self._model_spec,
                _value(meta, "prompt_token_count"),
                _value(meta, "candidates_token_count"),
                _value(meta, "cached_content_token_count"),
            )

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
        """Translate multiple texts in a single Gemini call.

        Sub-batches of 15 strings each with a small delay between them.
        Paid tier (2000 RPM) needs minimal delay; retry handles bursts.
        """
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
            config=self._config(2048, system_prompt),
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
            log.debug("Gemini test error: %s", msg)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                raise ConnectionError(
                    "Gemini: quota exceeded. "
                    "Wait for the daily reset or check your AI Studio limits."
                ) from e
            if "401" in msg or "403" in msg or "API_KEY_INVALID" in msg:
                raise ConnectionError("Gemini: invalid API key.") from e
            raise ConnectionError(f"Gemini: {e}") from e

    def get_name(self) -> str:
        return f"Gemini ({self.model_name})"


class GrokAPI(TranslationAPI):
    """xAI Grok 4 translation through Chat Completions."""

    supports_item_instructions = True
    supports_context = True

    def __init__(
        self,
        api_key: str,
        model: str = default_model("grok"),
    ):
        self.api_key = api_key
        self.model = normalize_model("grok", model)
        self._model_spec = get_model("grok", self.model)
        if self._model_spec:
            self._token_pricing = self._model_spec.token_pricing
        self.session = requests.Session()
        self.base_url = "https://api.x.ai/v1"
        self._reset_usage()

        self._extra_params: dict = {}
        profile = self._model_spec.request_profile if self._model_spec else ""
        if profile == "grok-no-reasoning":
            self._extra_params["reasoning_effort"] = "none"
        elif profile == "grok-low-reasoning":
            self._extra_params["reasoning_effort"] = "low"

    def _track_grok_response(self, data: dict) -> None:
        """Extract and track token usage from a Grok JSON response."""
        usage = data.get("usage")
        if usage:
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
        """Traduz texto usando Grok."""
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
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
                "max_tokens": 512,
                **self._extra_params,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self._track_grok_response(data)
        return data["choices"][0]["message"]["content"].strip()

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Translate multiple texts using Grok with sub-batches of 10."""
        import time as _time

        sub_batch_size = 10
        results: list[str] = []
        for start in range(0, len(texts), sub_batch_size):
            if start > 0 and self.batch_delay > 0:
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
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
                **self._extra_params,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        self._track_grok_response(data)
        content = data["choices"][0]["message"]["content"].strip()
        return parse_batch_response(content, expected_ids, texts)

    def test_connection(self) -> bool:
        """Testa conexão com Grok."""
        try:
            response = self.session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            log.debug("Grok test error: %s", e)
            raise ConnectionError(f"Grok: {e}") from e

    def get_name(self) -> str:
        return f"Grok ({self.model})"


class DeepSeekAPI(TranslationAPI):
    """DeepSeek V4 translation through its OpenAI-compatible endpoint."""

    supports_item_instructions = True
    supports_context = True

    def __init__(
        self,
        api_key: str,
        model: str = default_model("deepseek"),
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai: pip install openai")

        self.model = normalize_model("deepseek", model)
        self._model_spec = get_model("deepseek", self.model)
        if self._model_spec:
            self._token_pricing = self._model_spec.token_pricing
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=60.0,
            max_retries=0,
        )
        self._reset_usage()

    def _completion_options(self, max_tokens: int) -> dict:
        if (
            self._model_spec
            and self._model_spec.request_profile == "deepseek-no-thinking"
        ):
            return {
                "temperature": 1.3,
                "max_tokens": max_tokens,
                "extra_body": {"thinking": {"type": "disabled"}},
            }
        return {"temperature": 0.3, "max_tokens": max_tokens}

    def _track_deepseek_response(self, response) -> None:
        """Extract and track token usage from a DeepSeek response."""
        usage = getattr(response, "usage", None)
        if usage:
            cached = _value(usage, "prompt_cache_hit_tokens")
            uncached = _value(usage, "prompt_cache_miss_tokens")
            prompt_tokens = _value(usage, "prompt_tokens")
            if not prompt_tokens and (cached or uncached):
                prompt_tokens = cached + uncached
            _track_model_usage(
                self,
                self._model_spec,
                prompt_tokens,
                _value(usage, "completion_tokens"),
                cached,
            )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando DeepSeek."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
            getattr(self, "_item_instruction", ""),
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            **self._completion_options(512),
        )
        self._track_deepseek_response(response)
        content = response.choices[0].message.content
        return content.strip() if content else ""

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Translate multiple texts using DeepSeek with sub-batches of 15."""
        sub_batch_size = 15
        results: list[str] = []
        for start in range(0, len(texts), sub_batch_size):
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
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            **self._completion_options(4096),
        )
        self._track_deepseek_response(response)
        content = response.choices[0].message.content or ""
        return parse_batch_response(content, expected_ids, texts)

    def test_connection(self) -> bool:
        """Testa conexão com DeepSeek."""
        try:
            models = self.client.models.list()
            next(iter(models))
            return True
        except Exception as e:
            log.debug("DeepSeek test error: %s", e)
            raise ConnectionError(f"DeepSeek: {e}") from e

    def get_name(self) -> str:
        return f"DeepSeek ({self.model})"
