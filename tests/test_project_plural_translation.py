"""Focused tests for structural gettext plural translation."""

import re

import polib
import pytest

from core.languages import (
    GETTEXT_PLURAL_RULES,
    SUPPORTED_LANGUAGES,
    get_plural_rule,
)
from core.translator import (
    TranslationEngine,
    _plural_form_instruction,
    _plural_source,
)


class EchoAPI:
    batch_delay = 0
    supports_item_instructions = True
    supports_context = True

    def __init__(self):
        self.batch_calls = []
        self.instruction_calls = []

    def set_context(self, *_args):
        pass

    def translate_batch(self, texts, source_lang, target_lang):
        self.batch_calls.append(list(texts))
        return [f"{target_lang}:{text}" for text in texts]

    def translate(self, text, source_lang, target_lang):
        return f"{target_lang}:{text}"

    def translate_with_instruction(self, text, source_lang, target_lang, instruction):
        self.instruction_calls.append(instruction)
        example = re.search(r"example count (\d+)", instruction).group(1)
        category = {
            "0": "zero",
            "1": "one",
            "2": "few",
            "3": "three",
            "5": "many",
            "20": "other",
        }[example]
        return f"{target_lang}-{category}:{text}"


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
        ("ja", ["ja-one:{count} file"]),
        ("de", ["de-one:{count} file", "de-few:{count} files"]),
        (
            "ru",
            [
                "ru-one:{count} file",
                "ru-few:{count} files",
                "ru-many:{count} files",
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


def test_plural_source_matches_special_category_examples():
    entry = polib.POEntry(
        msgid="{count} file",
        msgid_plural="{count} files",
    )

    assert [_plural_source(entry, index, 4, "sl") for index in range(4)] == [
        "{count} files",
        "{count} file",
        "{count} files",
        "{count} files",
    ]
    assert [
        re.search(
            r"example count (\d+)",
            _plural_form_instruction("sl", index, 4),
        ).group(1)
        for index in range(4)
    ] == ["5", "1", "2", "3"]


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
        1: "ru-few:{count} files",
        2: "keep-two {count}",
    }
    assert api.batch_calls == []
    assert "example count 2" in api.instruction_calls[0]


def test_failed_plural_form_uses_source_stays_fuzzy_and_is_not_counted(tmp_path):
    class PluralFailureAPI:
        batch_delay = 0
        supports_item_instructions = True

        def set_context(self, *_args):
            pass

        def translate_batch(self, texts, source_lang, target_lang):
            raise RuntimeError("batch unavailable")

        def translate(self, text, source_lang, target_lang):
            if "files" in text:
                raise RuntimeError("plural unavailable")
            return f"translated:{text}"

        def translate_with_instruction(
            self, text, source_lang, target_lang, instruction
        ):
            return self.translate(text, source_lang, target_lang)

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


def test_plain_machine_translation_refuses_ambiguous_plural_categories(tmp_path):
    class PlainMachineTranslationAPI:
        batch_delay = 0

        def set_context(self, *_args):
            pass

        def translate_batch(self, texts, source_lang, target_lang):
            return [f"{target_lang}:{text}" for text in texts]

        def translate(self, text, source_lang, target_lang):
            return f"{target_lang}:{text}"

    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)
    engine = TranslationEngine(PlainMachineTranslationAPI(), "app")

    count = engine.translate_language(pot_path, "ru", tmp_path)

    entry = polib.pofile(str(tmp_path / "ru.po"))[0]
    assert count == 0
    assert entry.msgstr_plural == {
        0: "{count} file",
        1: "{count} files",
        2: "{count} files",
    }
    assert "fuzzy" in entry.flags
    assert engine.last_language_complete is False


def test_plain_machine_translation_still_handles_two_source_forms(tmp_path):
    class PlainMachineTranslationAPI:
        batch_delay = 0

        def set_context(self, *_args):
            pass

        def translate_batch(self, texts, source_lang, target_lang):
            return [f"{target_lang}:{text}" for text in texts]

        def translate(self, text, source_lang, target_lang):
            return f"{target_lang}:{text}"

    pot_path = tmp_path / "app.pot"
    _write_plural_catalog(pot_path)
    engine = TranslationEngine(PlainMachineTranslationAPI(), "app")

    count = engine.translate_language(pot_path, "de", tmp_path)

    entry = polib.pofile(str(tmp_path / "de.po"))[0]
    assert count == 1
    assert entry.msgstr_plural == {
        0: "de:{count} file",
        1: "de:{count} files",
    }
    assert "fuzzy" not in entry.flags
    assert engine.last_language_complete is True


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
        0: "de-one:{count} file",
        1: "de-few:{count} files",
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
    assert result == {"ru": True}
    assert entry.msgstr_plural == {
        0: "ru-one:{count} file",
        1: "ru-few:{count} files",
        2: "ru-many:{count} files",
    }
    assert all(entry.msgstr_plural.values())
    assert "fuzzy" not in entry.flags
