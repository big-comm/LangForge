"""Implementações de APIs de tradução pagas."""

import logging

import requests

from api.base import TranslationAPI, build_translation_prompt

log = logging.getLogger(__name__)


class OpenAIAPI(TranslationAPI):
    """API paga do OpenAI (GPT-4, GPT-4o-mini, etc)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai: pip install openai")

        self.client = OpenAI(api_key=api_key)
        self.model = model

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
        content = response.choices[0].message.content
        return content.strip() if content else ""

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

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        try:
            from google import genai
        except ImportError:
            raise ImportError("Install: pip install google-genai")

        self.client = genai.Client(api_key=api_key)
        self.model_name = model

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
            config={"temperature": 0.3, "max_output_tokens": 512},
        )
        return response.text.strip()

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

    def __init__(self, api_key: str, model: str = "grok-4-fast"):
        self.api_key = api_key
        self.model = model
        self.session = requests.Session()
        self.base_url = "https://api.x.ai/v1"

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
        return response.json()["choices"][0]["message"]["content"].strip()

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
