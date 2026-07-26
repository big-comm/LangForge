"""Focused tests for structural gettext plural translation."""

import polib
import pytest

from core.languages import (
    GETTEXT_PLURAL_RULES,
    SUPPORTED_LANGUAGES,
    get_plural_rule,
)
from core.translator import TranslationEngine


class EchoAPI:
    batch_delay = 0

    def __init__(self):
        self.batch_calls = []

    def set_context(self, *_args):
        pass

    def translate_batch(self, texts, source_lang, target_lang):
        self.batch_calls.append(list(texts))
        return [f"{target_lang}:{text}" for text in texts]

    def translate(self, text, source_lang, target_lang):
        return f"{target_lang}:{text}"


def _write_plural_catalog(path, forms=None):
    catalog = polib.POFile()
    catalog.append(
        polib.POEntry(
            msgid="{count} file",
            msgid_plural="{count} files",
            msgstr_plural=forms or {},
            flags=["python-brace-format"],
        )
    )
    catalog.save(str(path))


@pytest.mark.parametrize(
    ("lang", "forms", "header"),
    [
        ("ja", 1, "nplurals=1; plural=0;"),
        ("tr", 2, "nplurals=2; plural=(n != 1);"),
        ("he", 2, "nplurals=2; plural=(n != 1);"),
        (
            "ru",
            3,
            "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
            "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
        ),
        (
            "sl",
            4,
            "nplurals=4; plural=(n%100==1 ? 1 : n%100==2 ? 2 : "
            "n%100==3 || n%100==4 ? 3 : 0);",
        ),
    ],
)
def test_gettext_plural_rule_cardinalities(lang, forms, header):
    assert get_plural_rule(lang).forms == forms
    assert get_plural_rule(lang).header == header


def test_every_supported_language_has_plural_metadata():
    assert set(GETTEXT_PLURAL_RULES) == set(SUPPORTED_LANGUAGES) | {"sl"}
    assert "sl" not in SUPPORTED_LANGUAGES

    engine = TranslationEngine(EchoAPI(), "app")
    for lang in SUPPORTED_LANGUAGES:
        assert engine._create_metadata(lang)["Plural-Forms"] == (
            get_plural_rule(lang).header
        )

    assert get_plural_rule("pt-BR").header == "nplurals=2; plural=(n > 1);"
    assert get_plural_rule("is").header == (
        "nplurals=2; plural=(n%10!=1 || n%100==11);"
    )


@pytest.mark.parametrize(
    ("lang", "expected_forms"),
    [
        ("ja", ["ja:{count} files"]),
        ("de", ["de:{count} file", "de:{count} files"]),
        (
            "ru",
            [
                "ru:{count} file",
                "ru:{count} files",
                "ru:{count} files",
            ],
        ),
    ],
)
def test_translate_language_populates_target_plural_forms(
    tmp_path, lang, expected_forms
):
    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)
    api = EchoAPI()

    count = TranslationEngine(api, "app").translate_language(
        pot_path,
        lang,
        tmp_path,
    )

    translated = polib.pofile(str(tmp_path / f"{lang}.po"))
    entry = translated[0]
    assert count == 1
    assert entry.msgstr == ""
    assert entry.msgstr_plural == dict(enumerate(expected_forms))
    assert "fuzzy" not in entry.flags
    assert translated.metadata["Plural-Forms"] == get_plural_rule(lang).header


def test_partial_plural_preserves_existing_indexes_and_fills_missing(tmp_path):
    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)
    existing_path = tmp_path / "ru.po"
    _write_plural_catalog(
        existing_path,
        {
            0: "keep-zero {count}",
            2: "keep-two {count}",
            5: "drop-extra {count}",
        },
    )
    api = EchoAPI()

    count = TranslationEngine(api, "app").translate_language(
        pot_path,
        "ru",
        tmp_path,
    )

    entry = polib.pofile(str(existing_path))[0]
    assert count == 1
    assert entry.msgstr_plural == {
        0: "keep-zero {count}",
        1: "ru:{count} files",
        2: "keep-two {count}",
    }
    assert api.batch_calls == [["<x1/> files"]]


def test_failed_plural_form_uses_source_stays_fuzzy_and_is_not_counted(tmp_path):
    class PluralFailureAPI:
        batch_delay = 0

        def set_context(self, *_args):
            pass

        def translate_batch(self, texts, source_lang, target_lang):
            raise RuntimeError("batch unavailable")

        def translate(self, text, source_lang, target_lang):
            if "files" in text:
                raise RuntimeError("plural unavailable")
            return f"translated:{text}"

    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)

    engine = TranslationEngine(PluralFailureAPI(), "app")
    count = engine.translate_language(
        pot_path,
        "ru",
        tmp_path,
    )

    entry = polib.pofile(str(tmp_path / "ru.po"))[0]
    assert count == 0
    assert entry.msgstr_plural == {
        0: "translated:{count} file",
        1: "{count} files",
        2: "{count} files",
    }
    assert all(entry.msgstr_plural.values())
    assert "fuzzy" in entry.flags
    assert engine.last_language_complete is False


def test_project_result_reports_incomplete_language_as_failed(tmp_path):
    class FailureAPI:
        batch_delay = 0

        def set_context(self, *_args):
            pass

        def translate_batch(self, *_args):
            raise RuntimeError("batch unavailable")

        def translate(self, *_args):
            raise RuntimeError("translation unavailable")

    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)

    result = TranslationEngine(FailureAPI(), "app").translate_project(
        pot_path,
        tmp_path,
        languages=["ru"],
    )

    assert result == {"ru": False}


def test_fix_context_updates_plural_forms_without_using_msgstr(tmp_path):
    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)
    reference_path = tmp_path / "de.po"
    _write_plural_catalog(
        reference_path,
        {
            0: "old singular {count}",
            1: "old plural {count}",
        },
    )

    result = TranslationEngine(EchoAPI(), "app").fix_context(
        pot_path,
        tmp_path,
        reference_lang="de",
        languages=["de"],
    )

    translated = polib.pofile(str(reference_path))
    entry = translated[0]
    assert result == {"de": True}
    assert entry.msgstr == ""
    assert entry.msgstr_plural == {
        0: "de:{count} file",
        1: "de:{count} files",
    }
    assert translated.metadata["Plural-Forms"] == get_plural_rule("de").header


def test_fix_context_does_not_save_empty_plural_forms(tmp_path):
    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)
    reference_path = tmp_path / "ru.po"
    _write_plural_catalog(
        reference_path,
        {
            0: "existing singular {count}",
        },
    )

    result = TranslationEngine(EchoAPI(), "app").fix_context(
        pot_path,
        tmp_path,
        reference_lang="ru",
        languages=["ru"],
    )

    entry = polib.pofile(str(reference_path))[0]
    assert result == {}
    assert entry.msgstr_plural == {
        0: "existing singular {count}",
        1: "{count} files",
        2: "{count} files",
    }
    assert all(entry.msgstr_plural.values())
    assert "fuzzy" in entry.flags
