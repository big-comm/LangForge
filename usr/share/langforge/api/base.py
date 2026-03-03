"""Interface abstrata para APIs de tradução."""

from abc import ABC, abstractmethod
from typing import Optional

# Shared prompt template for all LLM-based translation APIs.
# Placeholders: {source}, {target}, {app_name}, {context_section}
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
        source=source,
        target=target,
        app_name=display_name,
        context_section=context_section,
    )


class TranslationAPI(ABC):
    """Classe base para todas as APIs de tradução."""

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
