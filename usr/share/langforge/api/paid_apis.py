"""Implementações de APIs de tradução pagas."""

from typing import Optional
from api.base import TranslationAPI


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
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "system",
                "content": f"You are a translator. Translate from {source_lang} to {target_lang}. Return ONLY the translation."
            }, {
                "role": "user",
                "content": text
            }],
            temperature=0.3,
            max_tokens=512
        )
        return response.choices[0].message.content.strip()

    def test_connection(self) -> bool:
        """Testa conexão com OpenAI."""
        try:
            self.client.models.retrieve(self.model)
            return True
        except Exception:
            return False

    def get_name(self) -> str:
        return f"OpenAI ({self.model})"


class GeminiAPI(TranslationAPI):
    """API paga do Google Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp"):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("Install google-generativeai: pip install google-generativeai")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model)
        self.model_name = model

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Gemini."""
        prompt = f"Translate from {source_lang} to {target_lang}. Return ONLY the translation:\n\n{text}"
        response = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 512
            }
        )
        return response.text.strip()

    def test_connection(self) -> bool:
        """Testa conexão com Gemini."""
        try:
            response = self.model.generate_content("test")
            return bool(response.text)
        except Exception:
            return False

    def get_name(self) -> str:
        return f"Gemini ({self.model_name})"


class ClaudeAPI(TranslationAPI):
    """API paga do Anthropic Claude."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5"):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("Install anthropic: pip install anthropic")

        self.client = Anthropic(api_key=api_key)
        self.model = model

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Claude."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=512,
            temperature=0.3,
            messages=[{
                "role": "user",
                "content": f"Translate from {source_lang} to {target_lang}. Return ONLY the translation:\n\n{text}"
            }]
        )
        return response.content[0].text.strip()

    def test_connection(self) -> bool:
        """Testa conexão com Claude."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "test"}]
            )
            return bool(response.content)
        except Exception:
            return False

    def get_name(self) -> str:
        return f"Claude ({self.model})"


class GrokAPI(TranslationAPI):
    """
    xAI Grok API - $25 créditos iniciais + $150/mês.
    Contexto: 2M tokens (maior do mercado)
    """

    def __init__(self, api_key: str, model: str = "grok-4-fast"):
        self.api_key = api_key
        self.model = model
        self.session = __import__('requests').Session()
        self.base_url = "https://api.x.ai/v1"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Grok."""
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{
                    "role": "system",
                    "content": f"Translate from {source_lang} to {target_lang}. Return ONLY the translation."
                }, {
                    "role": "user",
                    "content": text
                }],
                "temperature": 0.3,
                "max_tokens": 512
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()

    def test_connection(self) -> bool:
        """Testa conexão com Grok."""
        try:
            response = self.session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_name(self) -> str:
        return f"Grok ({self.model})"
