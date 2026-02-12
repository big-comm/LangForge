"""Factory para criação de clientes de API."""

from typing import Optional
from api.base import TranslationAPI


class APIFactory:
    """Factory para criar clientes de API de tradução."""

    # Mapeamento de providers para suas classes
    _FREE_APIS = {
        "deepl-free": ("api.free_apis", "DeepLFreeAPI"),
        "groq": ("api.free_apis", "GroqAPI"),
        "gemini-free": ("api.free_apis", "GeminiFreeAPI"),
        "openrouter": ("api.free_apis", "OpenRouterAPI"),
        "mistral-free": ("api.free_apis", "MistralFreeAPI"),
        "libretranslate": ("api.free_apis", "LibreTranslateAPI"),
    }

    _PAID_APIS = {
        "openai": ("api.paid_apis", "OpenAIAPI"),
        "gemini": ("api.paid_apis", "GeminiAPI"),
        "grok": ("api.paid_apis", "GrokAPI"),
    }

    @classmethod
    def create(cls, provider: str, api_key: str = "", **kwargs) -> TranslationAPI:
        """
        Cria cliente de API baseado no provider.

        Args:
            provider: Nome do provider (ex: 'groq', 'openai')
            api_key: Chave de API
            **kwargs: Argumentos adicionais (model, url, etc)

        Returns:
            Instância de TranslationAPI

        Raises:
            ValueError: Se provider for desconhecido
        """
        all_apis = {**cls._FREE_APIS, **cls._PAID_APIS}

        if provider not in all_apis:
            raise ValueError(f"Provider desconhecido: {provider}")

        module_name, class_name = all_apis[provider]

        # Import dinâmico
        import importlib
        module = importlib.import_module(module_name)
        api_class = getattr(module, class_name)

        # LibreTranslate usa URL ao invés de API key
        if provider == "libretranslate":
            url = kwargs.get("url", "https://libretranslate.com")
            return api_class(url)

        # Outros usam api_key e opcionalmente model
        model = kwargs.get("model")
        if model:
            return api_class(api_key, model)
        return api_class(api_key)

    @classmethod
    def create_from_settings(cls, settings) -> TranslationAPI:
        """
        Cria cliente de API baseado nas configurações salvas.

        Args:
            settings: Instância de Settings

        Returns:
            Instância de TranslationAPI
        """
        api_type = settings.get_api_type()

        if api_type == "free":
            provider = settings.get_free_provider()
            api_key = settings.get("free_api.api_key", "")

            if provider == "libretranslate":
                url = settings.get("free_api.libretranslate_url", "https://libretranslate.com")
                return cls.create(provider, url=url)

            return cls.create(provider, api_key)

        else:  # paid
            provider = settings.get_paid_provider()
            api_key = settings.get("paid_api.api_key", "")
            model = settings.get("paid_api.model", "")

            return cls.create(provider, api_key, model=model)

    @classmethod
    def get_free_providers(cls) -> list:
        """Retorna lista de providers gratuitos."""
        return list(cls._FREE_APIS.keys())

    @classmethod
    def get_paid_providers(cls) -> list:
        """Retorna lista de providers pagos."""
        return list(cls._PAID_APIS.keys())

    @classmethod
    def is_valid_provider(cls, provider: str) -> bool:
        """Verifica se provider é válido."""
        return provider in cls._FREE_APIS or provider in cls._PAID_APIS
