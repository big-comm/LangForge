"""Implementações de APIs de tradução pagas."""

from typing import Optional
from api.base import TranslationAPI


# Prompt contextual para tradução de software (LLM APIs)
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
                "content": TRANSLATION_PROMPT.format(
                    source=source_lang, target=target_lang
                )
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
            # List models is the simplest way to verify API key validity
            models = self.client.models.list()
            # Consume at least one result to confirm access
            next(iter(models))
            return True
        except Exception as e:
            import sys
            print(f"[LangForge] OpenAI test error: {e}", file=sys.stderr)
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
        prompt = (
            TRANSLATION_PROMPT.format(source=source_lang, target=target_lang)
            + f"\n\n{text}"
        )
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"temperature": 0.3, "max_output_tokens": 512}
        )
        return response.text.strip()

    def test_connection(self) -> bool:
        """Testa conexão com Gemini."""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents="test"
            )
            return bool(response.text)
        except Exception as e:
            import sys
            print(f"[LangForge] Gemini test error: {e}", file=sys.stderr)
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
                    "content": TRANSLATION_PROMPT.format(
                        source=source_lang, target=target_lang
                    )
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
            response.raise_for_status()
            return True
        except Exception as e:
            import sys
            print(f"[LangForge] Grok test error: {e}", file=sys.stderr)
            raise ConnectionError(f"Grok: {e}") from e

    def get_name(self) -> str:
        return f"Grok ({self.model})"
