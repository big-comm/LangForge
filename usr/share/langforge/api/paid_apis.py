"""Implementações de APIs de tradução pagas."""

import logging

import requests

from api.base import (
    TranslationAPI,
    build_batch_prompt,
    build_translation_prompt,
    clean_batch_parts,
    retry_on_rate_limit,
)

log = logging.getLogger(__name__)

# Maximum strings per batch call (keep token usage under control)
_BATCH_SIZE = 15


class OpenAIAPI(TranslationAPI):
    """API paga do OpenAI (GPT-4, GPT-4o-mini, etc)."""

    # GPT-4o-mini pricing (USD per 1M tokens)
    _token_pricing = (0.15, 0.60)

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai: pip install openai")

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self._reset_usage()

    def _track_openai_response(self, response) -> None:
        """Extract and track token usage from an OpenAI response."""
        usage = getattr(response, "usage", None)
        if usage:
            self._track_usage(
                usage.prompt_tokens or 0,
                usage.completion_tokens or 0,
            )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando OpenAI."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=512,
        )
        self._track_openai_response(response)
        content = response.choices[0].message.content
        return content.strip() if content else ""

    def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        """Translate multiple texts in a single OpenAI call."""
        if len(texts) == 1:
            return [self.translate(texts[0], source_lang, target_lang)]
        return self._do_batch(texts, source_lang, target_lang)

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
        user_msg = "|||NEXT|||".join(texts)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        self._track_openai_response(response)
        content = response.choices[0].message.content or ""
        parts = clean_batch_parts(content)
        if len(parts) != len(texts):
            log.warning(
                "Batch mismatch: expected %d, got %d. Translating remaining individually.",
                len(texts),
                len(parts),
            )
            if len(parts) < len(texts):
                for t in texts[len(parts):]:
                    parts.append(self.translate(t, source_lang, target_lang))
            else:
                parts = parts[: len(texts)]
        return parts

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

    batch_delay = 0.1  # Paid tier has 2000 RPM; retry handles bursts
    # Gemini Flash pricing (USD per 1M tokens) — non-thinking output
    _token_pricing = (0.15, 0.60)

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        try:
            from google import genai
        except ImportError:
            raise ImportError("Install: pip install google-genai")

        self.client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 60_000},
        )
        self.model_name = model
        self._reset_usage()

        # Disable thinking for 2.5+ models — translation doesn't need it
        # and thinking tokens cost 6x more ($3.50 vs $0.60 per 1M)
        self._no_think = {}
        if "2.5" in model or "2.6" in model:
            self._no_think = {"thinking_config": {"thinking_budget": 0}}

    def _track_gemini_response(self, response) -> None:
        """Extract and track token usage from a Gemini response."""
        meta = getattr(response, "usage_metadata", None)
        if meta:
            self._track_usage(
                meta.prompt_token_count or 0,
                meta.candidates_token_count or 0,
            )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Gemini."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
        )
        prompt = f"{system_prompt}\n\n{text}"
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"temperature": 0.3, "max_output_tokens": 512, **self._no_think},
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

        if len(texts) == 1:
            return [self.translate(texts[0], source_lang, target_lang)]
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
        user_msg = "|||NEXT|||".join(texts)
        prompt = f"{system_prompt}\n\n{user_msg}"
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"temperature": 0.3, "max_output_tokens": 2048, **self._no_think},
        )
        self._track_gemini_response(response)
        parts = clean_batch_parts(response.text)
        if len(parts) != len(texts):
            log.warning(
                "Batch mismatch: expected %d, got %d. Translating remaining individually.",
                len(texts),
                len(parts),
            )
            # Keep good partial results, translate the rest individually
            if len(parts) < len(texts):
                for t in texts[len(parts) :]:
                    parts.append(self.translate(t, source_lang, target_lang))
            else:
                parts = parts[: len(texts)]
        return parts

    def test_connection(self) -> bool:
        """Testa conexão com Gemini."""
        try:
            response = self.client.models.generate_content(
                model=self.model_name, contents="test"
            )
            return bool(response.text)
        except Exception as e:
            log.debug("Gemini test error: %s", e)
            raise ConnectionError(f"Gemini: {e}") from e

    def get_name(self) -> str:
        return f"Gemini ({self.model_name})"


class GrokAPI(TranslationAPI):
    """
    xAI Grok API - $25 créditos iniciais + $150/mês.
    Contexto: 2M tokens (maior do mercado)
    """

    # Grok pricing (USD per 1M tokens) — grok-4-fast
    _token_pricing = (3.00, 15.00)

    def __init__(self, api_key: str, model: str = "grok-4-fast"):
        self.api_key = api_key
        self.model = model
        self.session = requests.Session()
        self.base_url = "https://api.x.ai/v1"
        self._reset_usage()

    def _track_grok_response(self, data: dict) -> None:
        """Extract and track token usage from a Grok JSON response."""
        usage = data.get("usage")
        if usage:
            self._track_usage(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

    @retry_on_rate_limit
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Grok."""
        system_prompt = build_translation_prompt(
            source_lang,
            target_lang,
            getattr(self, "_app_name", ""),
            getattr(self, "_context_entries", None),
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
        """Translate multiple texts in a single Grok call."""
        if len(texts) == 1:
            return [self.translate(texts[0], source_lang, target_lang)]
        return self._do_batch(texts, source_lang, target_lang)

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
        user_msg = "|||NEXT|||".join(texts)
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
                "max_tokens": 2048,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        self._track_grok_response(data)
        content = data["choices"][0]["message"]["content"].strip()
        parts = clean_batch_parts(content)
        if len(parts) != len(texts):
            log.warning(
                "Grok batch mismatch: expected %d, got %d. Translating remaining individually.",
                len(texts),
                len(parts),
            )
            if len(parts) < len(texts):
                for t in texts[len(parts):]:
                    parts.append(self.translate(t, source_lang, target_lang))
            else:
                parts = parts[: len(texts)]
        return parts

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
