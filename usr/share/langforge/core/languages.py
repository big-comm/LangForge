"""Constantes de idiomas suportados para tradução."""

from typing import NamedTuple


class PluralRule(NamedTuple):
    """GNU gettext plural rule for a language."""

    forms: int
    header: str


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

_ONE_FORM = PluralRule(1, "nplurals=1; plural=0;")
_TWO_FORMS = PluralRule(2, "nplurals=2; plural=(n != 1);")
_TWO_FORMS_ZERO_ONE = PluralRule(2, "nplurals=2; plural=(n > 1);")
_CZECH_SLOVAK_FORMS = PluralRule(
    3,
    "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
)
_SLAVIC_FORMS = PluralRule(
    3,
    "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
    "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
)

# `sl` is a generic four-form reference without being enabled in the UI.
GETTEXT_PLURAL_RULES = {
    "bg": _TWO_FORMS,
    "cs": _CZECH_SLOVAK_FORMS,
    "da": _TWO_FORMS,
    "de": _TWO_FORMS,
    "el": _TWO_FORMS,
    "en": _TWO_FORMS,
    "es": _TWO_FORMS,
    "et": _TWO_FORMS,
    "fi": _TWO_FORMS,
    "fr": _TWO_FORMS_ZERO_ONE,
    "he": _TWO_FORMS,
    "hr": _SLAVIC_FORMS,
    "hu": _TWO_FORMS,
    "is": PluralRule(
        2,
        "nplurals=2; plural=(n%10!=1 || n%100==11);",
    ),
    "it": _TWO_FORMS,
    "ja": _ONE_FORM,
    "ko": _ONE_FORM,
    "nl": _TWO_FORMS,
    "no": _TWO_FORMS,
    "pl": PluralRule(
        3,
        "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && "
        "(n%100<10 || n%100>=20) ? 1 : 2);",
    ),
    "pt-BR": _TWO_FORMS_ZERO_ONE,
    "pt": _TWO_FORMS,
    "ro": PluralRule(
        3,
        "nplurals=3; plural=(n==1 ? 0 : (n==0 || "
        "(n%100 > 0 && n%100 < 20)) ? 1 : 2);",
    ),
    "ru": _SLAVIC_FORMS,
    "sk": _CZECH_SLOVAK_FORMS,
    "sv": _TWO_FORMS,
    "tr": _TWO_FORMS,
    "uk": _SLAVIC_FORMS,
    "zh": _ONE_FORM,
    "sl": PluralRule(
        4,
        "nplurals=4; plural=(n%100==1 ? 1 : n%100==2 ? 2 : "
        "n%100==3 || n%100==4 ? 3 : 0);",
    ),
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
    "pt-BR": "por-BR",
    "pt": "por",
    "ro": "rum",
    "ru": "rus",
    "sk": "slo",
    "sv": "swe",
    "tr": "tur",
    "uk": "ukr",
    "zh": "chi",
}


def get_plural_rule(lang: str) -> PluralRule:
    """Return the GNU gettext plural rule for a language."""
    try:
        return GETTEXT_PLURAL_RULES[lang]
    except KeyError as error:
        raise ValueError(f"Unsupported plural rules for language: {lang}") from error


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
