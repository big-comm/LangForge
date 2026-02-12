"""Constantes de idiomas suportados para tradução."""

SUPPORTED_LANGUAGES = {
    'bg': 'Bulgarian',
    'cs': 'Czech',
    'da': 'Danish',
    'de': 'German',
    'el': 'Greek',
    'en': 'English',
    'es': 'Spanish',
    'et': 'Estonian',
    'fi': 'Finnish',
    'fr': 'French',
    'he': 'Hebrew',
    'hr': 'Croatian',
    'hu': 'Hungarian',
    'is': 'Icelandic',
    'it': 'Italian',
    'ja': 'Japanese',
    'ko': 'Korean',
    'nl': 'Dutch',
    'no': 'Norwegian',
    'pl': 'Polish',
    'pt-BR': 'Portuguese (Brazil)',
    'pt': 'Portuguese',
    'ro': 'Romanian',
    'ru': 'Russian',
    'sk': 'Slovak',
    'sv': 'Swedish',
    'tr': 'Turkish',
    'uk': 'Ukrainian',
    'zh': 'Chinese'
}

# Mapeamento de códigos de idioma para LibreTranslate/APIs
LANGUAGE_CODE_MAP = {
    'pt-BR': 'pt',  # LibreTranslate usa 'pt' para português
    'no': 'nb',     # Norwegian Bokmål
    'he': 'iw',     # Hebrew alternativo
    'zh': 'zh-CN',  # Chinese simplificado
}

def get_api_lang_code(lang: str) -> str:
    """Converte código de idioma para formato da API."""
    return LANGUAGE_CODE_MAP.get(lang, lang)
