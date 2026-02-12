"""Implementações de APIs de tradução com tier gratuito."""

import requests
from typing import Optional
from api.base import TranslationAPI
from core.languages import get_api_lang_code


class GroqAPI(TranslationAPI):
    """
    Groq - Super rápido com LPU hardware.
    Limite: 14,400 requests/dia (melhor opção gratuita!)
    Qualidade: Excelente
    Velocidade: Mais rápida do mercado
    """

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = api_key
        self.model = model
        self.session = requests.Session()
        self.base_url = "https://api.groq.com/openai/v1"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Groq."""
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{
                    "role": "system",
                    "content": f"Translate from {source_lang} to {target_lang}. Return ONLY the translation. IMPORTANT: preserve any XML tags like <x1/>, <x2/> etc. exactly as they are, do not translate or modify them."
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
        """Testa conexão com Groq."""
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
        return "Groq (14.4k req/dia)"


class LibreTranslateAPI(TranslationAPI):
    """
    LibreTranslate - API opensource gratuita.
    Limite: Ilimitado (API pública pode ter rate limit)
    Qualidade: Boa
    """

    def __init__(self, url: str = "https://libretranslate.com"):
        self.url = url.rstrip('/')
        self.session = requests.Session()

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando LibreTranslate."""
        source = get_api_lang_code(source_lang)
        target = get_api_lang_code(target_lang)

        response = self.session.post(
            f"{self.url}/translate",
            json={
                "q": text,
                "source": source,
                "target": target,
                "format": "text"
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["translatedText"]

    def test_connection(self) -> bool:
        """Testa conexão com LibreTranslate."""
        try:
            response = self.session.get(f"{self.url}/languages", timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    def get_name(self) -> str:
        return "LibreTranslate"


class DeepLFreeAPI(TranslationAPI):
    """
    DeepL Free API - Best translation quality.
    Limit: 500,000 characters/month
    Quality: Excellent (better than Google)
    Requires: Free API key from https://www.deepl.com/pro-api
    """

    # DeepL supported target languages (as of 2024)
    DEEPL_LANG_MAP = {
        'bg': 'BG',
        'cs': 'CS',
        'da': 'DA',
        'de': 'DE',
        'el': 'EL',
        'en': 'EN-US',
        'es': 'ES',
        'et': 'ET',
        'fi': 'FI',
        'fr': 'FR',
        'hu': 'HU',
        'it': 'IT',
        'ja': 'JA',
        'ko': 'KO',
        'nl': 'NL',
        'no': 'NB',  # Norwegian Bokmål
        'pl': 'PL',
        'pt-BR': 'PT-BR',
        'pt': 'PT-PT',
        'ro': 'RO',
        'ru': 'RU',
        'sk': 'SK',
        'sv': 'SV',
        'tr': 'TR',
        'uk': 'UK',
        'zh': 'ZH',
        # Not supported by DeepL: he (Hebrew), hr (Croatian), is (Icelandic)
    }

    def __init__(self, api_key: str):
        import time
        self.api_key = api_key
        self.session = requests.Session()
        self.base_url = "https://api-free.deepl.com/v2"
        self._time = time
        self._last_request = 0

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text using DeepL."""
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
            timeout=30
        )
        self._last_request = self._time.time()

        if response.status_code == 456:
            raise RuntimeError("DeepL quota exceeded (500k chars/month)")
        response.raise_for_status()
        return response.json()["translations"][0]["text"]

    def test_connection(self) -> bool:
        """Test connection with DeepL."""
        try:
            response = self.session.get(
                f"{self.base_url}/usage",
                headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                timeout=10
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_name(self) -> str:
        return "DeepL Free (500k chars/month)"


class GeminiFreeAPI(TranslationAPI):
    """
    Google Gemini Flash - Free tier generoso.
    Limite: 1,000 requests/dia (Flash-Lite), 15 RPM
    Qualidade: Excelente
    API Key gratuita em: https://aistudio.google.com/apikey
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-lite"):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("Install: pip install google-generativeai")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model)
        self.model_name = model

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Gemini."""
        prompt = f"Translate from {source_lang} to {target_lang}. Return ONLY the translation. IMPORTANT: preserve any XML tags like <x1/>, <x2/> etc. exactly as they are, do not translate or modify them.\n\n{text}"
        response = self.model.generate_content(
            prompt,
            generation_config={"temperature": 0.3, "max_output_tokens": 512}
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
        return "Gemini Free (1k req/dia)"


class OpenRouterAPI(TranslationAPI):
    """
    OpenRouter - 18 modelos gratuitos.
    Limite: Varia por modelo (muitos ilimitados)
    Qualidade: Excelente (Meta, Mistral, NVIDIA)
    API Key gratuita em: https://openrouter.ai/
    """

    def __init__(self, api_key: str, model: str = "meta-llama/llama-3.1-8b-instruct:free"):
        self.api_key = api_key
        self.model = model
        self.session = requests.Session()
        self.base_url = "https://openrouter.ai/api/v1"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando OpenRouter."""
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://github.com/translation-automator",
                "X-Title": "Translation Automator"
            },
            json={
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": f"Translate from {source_lang} to {target_lang}. Return ONLY the translation. IMPORTANT: preserve any XML tags like <x1/>, <x2/> etc. exactly as they are.\n\n{text}"
                }],
                "temperature": 0.3,
                "max_tokens": 512
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()

    def test_connection(self) -> bool:
        """Testa conexão com OpenRouter."""
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
        return "OpenRouter (18 modelos grátis)"


class MistralFreeAPI(TranslationAPI):
    """
    Mistral - Tier "Experiment" gratuito.
    Limite: Generoso para teste
    Qualidade: Excelente
    API Key gratuita em: https://console.mistral.ai/
    """

    def __init__(self, api_key: str, model: str = "mistral-small-latest"):
        self.api_key = api_key
        self.model = model
        self.session = requests.Session()
        self.base_url = "https://api.mistral.ai/v1"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Traduz texto usando Mistral."""
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": f"Translate from {source_lang} to {target_lang}. Return ONLY the translation. IMPORTANT: preserve any XML tags like <x1/>, <x2/> etc. exactly as they are.\n\n{text}"
                }],
                "temperature": 0.3,
                "max_tokens": 512
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()

    def test_connection(self) -> bool:
        """Testa conexão com Mistral."""
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
        return "Mistral Free"
