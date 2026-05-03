"""Constantes de idiomas suportados para tradução."""

SUPPORTED_LANGUAGES = {
    "bg": "Bulgarian",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hr": "Croatian",
    "hu": "Hungarian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt-BR": "Portuguese (Brazil)",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sv": "Swedish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "zh": "Chinese",
}

# Mapeamento de códigos de idioma para LibreTranslate/APIs
LANGUAGE_CODE_MAP = {
    "pt-BR": "pt",  # LibreTranslate usa 'pt' para português
    "no": "nb",  # Norwegian Bokmål
    "he": "iw",  # Hebrew alternativo
    "zh": "zh-CN",  # Chinese simplificado
}

# ISO 639-2/B 3-letter codes for file naming (subtitles, md, txt)
FILE_LANG_CODES = {
    "bg": "bul",
    "cs": "cze",
    "da": "dan",
    "de": "ger",
    "el": "gre",
    "en": "eng",
    "es": "spa",
    "et": "est",
    "fi": "fin",
    "fr": "fre",
    "he": "heb",
    "hr": "hrv",
    "hu": "hun",
    "is": "ice",
    "it": "ita",
    "ja": "jpn",
    "ko": "kor",
    "nl": "dut",
    "no": "nor",
    "pl": "pol",
    "pt-BR": "por",
    "pt": "por",
    "ro": "rum",
    "ru": "rus",
    "sk": "slo",
    "sv": "swe",
    "tr": "tur",
    "uk": "ukr",
    "zh": "chi",
}


def get_api_lang_code(lang: str) -> str:
    """Converte código de idioma para formato da API."""
    return LANGUAGE_CODE_MAP.get(lang, lang)


def to_gettext_locale(lang: str) -> str:
    """Convert a BCP-47 lang code to POSIX/gettext locale form.

    gettext .po/.mo files and locale directories use underscore between
    language and region (pt_BR), not hyphen (pt-BR which is BCP-47 / web).
    """
    return lang.replace("-", "_")


def resolve_po_path(locale_dir, lang: str):
    """Return the .po path for a language, preferring POSIX form.

    If a legacy hyphenated .po already exists and the underscored one
    doesn't, return the legacy path so existing translations are not
    orphaned. Otherwise return the underscored form (the standard).
    """
    from pathlib import Path

    locale_dir = Path(locale_dir)
    posix_path = locale_dir / f"{to_gettext_locale(lang)}.po"
    legacy_path = locale_dir / f"{lang}.po"
    if "-" in lang and legacy_path.exists() and not posix_path.exists():
        return legacy_path
    return posix_path


def get_file_lang_code(lang: str) -> str:
    """Return 3-letter ISO 639-2/B code for file naming."""
    return FILE_LANG_CODES.get(lang, lang)
