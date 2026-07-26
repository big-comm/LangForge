"""Tests for core.translator placeholder protection and validation."""

import json
import sys
import threading
from pathlib import Path

import polib
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "usr" / "share" / "langforge"))

from core.translator import (
    TranslationEngine,
    _save_po_atomic,
    _protect_placeholders,
    _restore_placeholders,
    _validate_placeholders,
    _fix_placeholders,
    _is_translation_plausible,
    _match_newlines,
)
from api.base import build_translation_prompt, TranslationAPI, clean_batch_parts, prepare_batch_texts, restore_batch_texts


class TestProtectPlaceholders:
    def test_simple_percent_s(self):
        text = "Hello %s world"
        protected, tokens = _protect_placeholders(text)
        assert "%s" not in protected
        assert len(tokens) == 1
        assert tokens[0][1] == "%s"

    def test_named_format(self):
        text = "%(name)s has %(count)d items"
        protected, tokens = _protect_placeholders(text)
        assert "%(name)s" not in protected
        assert "%(count)d" not in protected
        assert len(tokens) == 2

    def test_curly_braces(self):
        text = "Hello {name}, you have {count} items"
        protected, tokens = _protect_placeholders(text)
        assert "{name}" not in protected
        assert "{count}" not in protected
        assert len(tokens) == 2

    def test_no_placeholders(self):
        text = "Hello world"
        protected, tokens = _protect_placeholders(text)
        assert protected == text
        assert len(tokens) == 0

    def test_mixed_formats(self):
        text = "%(name)s has %d items in {folder}"
        protected, tokens = _protect_placeholders(text)
        assert len(tokens) == 3

    def test_width_precision_length_and_positional_printf(self):
        text = "%(value)08.2f | %1$.*2$f | %zu | %%"
        protected, tokens = _protect_placeholders(text)

        assert len(tokens) == 4
        assert [placeholder for _, placeholder in tokens] == [
            "%(value)08.2f",
            "%1$.*2$f",
            "%zu",
            "%%",
        ]
        assert _restore_placeholders(protected, tokens) == text

    def test_escaped_curly_braces_are_not_placeholders(self):
        protected, tokens = _protect_placeholders(
            "{{literal}} and {value:{width}.2f}"
        )

        assert tokens == [("<x1/>", "{value:{width}.2f}")]
        assert protected == "{{literal}} and <x1/>"

    def test_empty_string(self):
        text = ""
        protected, tokens = _protect_placeholders(text)
        assert protected == ""
        assert len(tokens) == 0


class TestRestorePlaceholders:
    def test_roundtrip_percent_s(self):
        original = "Hello %s world"
        protected, tokens = _protect_placeholders(original)
        restored = _restore_placeholders(protected, tokens)
        assert restored == original

    def test_roundtrip_named(self):
        original = "%(user)s logged in at %(time)s"
        protected, tokens = _protect_placeholders(original)
        restored = _restore_placeholders(protected, tokens)
        assert restored == original

    def test_roundtrip_curly(self):
        original = "Error in {module}: {message}"
        protected, tokens = _protect_placeholders(original)
        restored = _restore_placeholders(protected, tokens)
        assert restored == original

    def test_roundtrip_complex(self):
        original = "%(name)s has %d items in {folder} at %s"
        protected, tokens = _protect_placeholders(original)
        restored = _restore_placeholders(protected, tokens)
        assert restored == original


class TestValidatePlaceholders:
    def test_valid_same_placeholders(self):
        assert _validate_placeholders("Hello %s", "Olá %s")

    def test_invalid_missing_placeholder(self):
        assert not _validate_placeholders("Hello %s", "Olá")

    def test_unnumbered_printf_reordering_is_invalid(self):
        assert not _validate_placeholders("%s has %d items", "%d itens de %s")

    def test_numbered_printf_reordering_is_valid(self):
        assert _validate_placeholders(
            "%1$s has %2$d items",
            "%2$d itens de %1$s",
        )

    def test_named_reordering_is_valid(self):
        assert _validate_placeholders(
            "{name} has {count} items",
            "{count} itens de {name}",
        )

    def test_valid_no_placeholders(self):
        assert _validate_placeholders("Hello", "Olá")

    def test_invalid_extra_placeholder(self):
        assert not _validate_placeholders("Hello", "Olá %s")


class TestFixPlaceholders:
    def test_adds_missing_placeholder(self):
        result = _fix_placeholders("Hello %s", "Olá")
        assert "%s" in result

    def test_preserves_correct_translation(self):
        result = _fix_placeholders("Hello %s", "Olá %s")
        assert result == "Olá %s"

    def test_repairs_unsafe_unnumbered_reordering_without_cascade(self):
        result = _fix_placeholders("%s has %d items", "%d itens de %s")

        assert result == "%s itens de %d"
        assert _validate_placeholders("%s has %d items", result)


class TestBuildTranslationPrompt:
    def test_includes_app_name(self):
        prompt = build_translation_prompt("en", "pt-BR", app_name="ashy-term")
        assert "Ashy Term" in prompt

    def test_app_name_in_do_not_translate_rule(self):
        prompt = build_translation_prompt("en", "pt-BR", app_name="ashy-term")
        assert "NEVER translate the application name" in prompt
        # App name must appear at least twice (intro + rule)
        assert prompt.count("Ashy Term") >= 2

    def test_includes_source_and_target(self):
        prompt = build_translation_prompt("en", "pt-BR")
        assert "English" in prompt
        assert "Portuguese (Brazil)" in prompt

    def test_context_entries_included(self):
        entries = ["Open File", "Save As", "Preferences"]
        prompt = build_translation_prompt("en", "pt-BR", context_entries=entries)
        assert "Open File" in prompt
        assert "Save As" in prompt
        assert "Preferences" in prompt

    def test_no_context_section_when_empty(self):
        prompt = build_translation_prompt("en", "pt-BR", app_name="myapp")
        assert "other UI strings" not in prompt

    def test_context_limited_to_15(self):
        entries = [f"String {i}" for i in range(25)]
        prompt = build_translation_prompt("en", "pt-BR", context_entries=entries)
        assert "String 14" in prompt
        assert "String 15" not in prompt

    def test_unknown_app_name_when_empty(self):
        prompt = build_translation_prompt("en", "pt-BR", app_name="")
        assert "unknown" in prompt

    def test_preserves_proper_noun_rule(self):
        prompt = build_translation_prompt("en", "pt-BR", app_name="my-app")
        assert "proper noun" in prompt
        assert "brand names" in prompt

    def test_textdomain_with_underscores(self):
        prompt = build_translation_prompt("en", "de", app_name="my_cool_app")
        assert "My Cool App" in prompt


class TestSetContext:
    def test_set_context_stores_values(self):
        class DummyAPI(TranslationAPI):
            def translate(self, text, source_lang, target_lang):
                return text
            def test_connection(self):
                return True
            def get_name(self):
                return "Dummy"

        api = DummyAPI()
        api.set_context("ashy-term", ["Open", "Close", "Settings"])
        assert api._app_name == "ashy-term"
        assert api._context_entries == ["Open", "Close", "Settings"]

    def test_set_context_defaults_empty_list(self):
        class DummyAPI(TranslationAPI):
            def translate(self, text, source_lang, target_lang):
                return text
            def test_connection(self):
                return True
            def get_name(self):
                return "Dummy"

        api = DummyAPI()
        api.set_context("myapp")
        assert api._app_name == "myapp"
        assert api._context_entries == []


class TestBatchNewlineNormalization:
    def test_prepare_replaces_newlines(self):
        texts = ["Line1\nLine2", "Single"]
        safe = prepare_batch_texts(texts)
        assert "\n" not in safe[0]
        assert "<NL>" in safe[0]
        # prepare now adds [N] numbering prefix for batch alignment
        assert safe[1] == "[2] Single"

    def test_restore_reverts_placeholder(self):
        parts = ["Linha1 <NL> Linha2", "Simples"]
        restored = restore_batch_texts(parts)
        assert restored[0] == "Linha1\nLinha2"
        assert restored[1] == "Simples"

    def test_restore_strips_stray_separator(self):
        parts = ["- Fala A|||NEXT|||– Fala B"]
        restored = restore_batch_texts(parts)
        assert "|||NEXT|||" not in restored[0]
        assert restored[0] == "- Fala A– Fala B"

    def test_roundtrip(self):
        originals = ["First\nSecond\nThird", "No newlines", "A\nB"]
        safe = prepare_batch_texts(originals)
        restored = restore_batch_texts(safe)
        assert restored == originals

    def test_empty_list(self):
        assert prepare_batch_texts([]) == []
        assert restore_batch_texts([]) == []

    def test_clean_batch_parts_basic(self):
        raw = "One|||NEXT|||Two|||NEXT|||Three"
        assert clean_batch_parts(raw) == ["One", "Two", "Three"]

    def test_clean_batch_parts_strips_leading_separator(self):
        raw = "|||NEXT|||One|||NEXT|||Two"
        assert clean_batch_parts(raw) == ["One", "Two"]

    def test_clean_batch_parts_strips_trailing_empty(self):
        raw = "One|||NEXT|||Two|||NEXT|||"
        assert clean_batch_parts(raw) == ["One", "Two"]

    def test_clean_batch_parts_strips_numbering(self):
        raw = "[1] First|||NEXT|||[2] Second|||NEXT|||[3] Third"
        assert clean_batch_parts(raw) == ["First", "Second", "Third"]

    def test_clean_batch_parts_mixed_numbering(self):
        """LLM echoes numbering on some parts but not all."""
        raw = "[1] First|||NEXT|||Second|||NEXT|||[3] Third"
        assert clean_batch_parts(raw) == ["First", "Second", "Third"]

    def test_prepare_batch_adds_numbering(self):
        texts = ["Alpha", "Beta", "Gamma"]
        safe = prepare_batch_texts(texts)
        assert safe == ["[1] Alpha", "[2] Beta", "[3] Gamma"]

    def test_restore_batch_strips_numbering(self):
        parts = ["[1] Alpha", "[2] Beta"]
        restored = restore_batch_texts(parts)
        assert restored == ["Alpha", "Beta"]

    def test_restore_nl_without_trailing_space(self):
        """LLM drops trailing space from <NL> at end of string."""
        parts = ["Copyright Team <NL>  <NL>"]
        restored = restore_batch_texts(parts)
        assert restored[0] == "Copyright Team\n\n"

    def test_restore_nl_preserves_trailing_newlines(self):
        """Trailing newlines must not be stripped."""
        safe = prepare_batch_texts(["Line1\n\n"])
        restored = restore_batch_texts(safe)
        assert restored == ["Line1\n\n"]

    def test_restore_nl_no_false_positive(self):
        """Text without <NL> should not be altered."""
        parts = ["Simple text"]
        restored = restore_batch_texts(parts)
        assert restored == ["Simple text"]


class TestMatchNewlines:
    def test_trailing_newlines_added(self):
        assert _match_newlines("Hello\n\n", "Olá") == "Olá\n\n"

    def test_trailing_newlines_removed(self):
        assert _match_newlines("Hello", "Olá\n\n") == "Olá"

    def test_leading_newlines_added(self):
        assert _match_newlines("\nHello", "Olá") == "\nOlá"

    def test_matching_newlines_unchanged(self):
        assert _match_newlines("Hello\n", "Olá\n") == "Olá\n"

    def test_empty_msgstr(self):
        assert _match_newlines("Hello\n", "") == ""

    def test_no_newlines(self):
        assert _match_newlines("Hello", "Olá") == "Olá"
    def test_normal_translation(self):
        assert _is_translation_plausible("Hello world", "Olá mundo") is True

    def test_empty_strings(self):
        assert _is_translation_plausible("", "") is True
        assert _is_translation_plausible("Hello", "") is True

    def test_extremely_long_translation(self):
        """5x+ length ratio should be flagged."""
        short = "OK"
        very_long = "A" * 100
        assert _is_translation_plausible(short, very_long) is False

    def test_extremely_short_translation(self):
        """<0.1x length ratio should be flagged."""
        long_text = "This is a very long sentence with many words in it here"
        assert _is_translation_plausible(long_text, "A") is False

    def test_borderline_length_ok(self):
        """4x ratio should still pass (threshold is 5x)."""
        text = "Hello"
        assert _is_translation_plausible(text, "A" * 20) is True

    def test_spurious_placeholders(self):
        """If original has no placeholders but translation does → reject."""
        assert _is_translation_plausible("Hello", "Olá %s") is False
        assert _is_translation_plausible("Settings", "Configurações {name}") is False

    def test_both_have_placeholders(self):
        """Both having placeholders should pass (mismatch caught elsewhere)."""
        assert _is_translation_plausible("Hello %s", "Olá %s") is True

    def test_no_placeholders_either(self):
        assert _is_translation_plausible("Hello", "Olá") is True


class TestRestorePlaceholdersCorruption:
    """Tests for improved _restore_placeholders handling of LLM corruptions."""

    def test_extra_space_in_token(self):
        text = "Olá <x1 /> mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == "Olá %s mundo"

    def test_leading_space_in_token(self):
        text = "Olá < x1/> mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == "Olá %s mundo"

    def test_uppercase_token(self):
        text = "Olá <X1/> mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == "Olá %s mundo"

    def test_html_encoded_token(self):
        text = "Olá &lt;x1/&gt; mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == "Olá %s mundo"

    def test_missing_slash_token(self):
        text = "Olá <x1> mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == "Olá %s mundo"

    def test_bracket_variant(self):
        text = "Olá [x1] mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == "Olá %s mundo"

    def test_stripped_tags(self):
        text = "Olá x1 mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == "Olá %s mundo"

    def test_residual_xml_cleanup(self):
        """Unknown residual XML tokens should be removed."""
        text = "Olá <x99/> mundo"
        tokens = []  # No tokens to restore, but residual should be cleaned
        assert _restore_placeholders(text, tokens) == "Olá  mundo"

    def test_multiple_corruptions(self):
        text = "A <X1/> B <x2 /> C"
        tokens = [("<x1/>", "%s"), ("<x2/>", "%d")]
        result = _restore_placeholders(text, tokens)
        assert result == "A %s B %d C"


class TestFixPlaceholdersAdvanced:
    """Tests for improved _fix_placeholders with spurious/extra removal."""

    def test_removes_spurious_placeholders(self):
        """If original has no placeholders, remove any LLM added."""
        original = "Hello world"
        translated = "Olá %s mundo"
        result = _fix_placeholders(original, translated)
        assert "%s" not in result

    def test_removes_extra_curly_placeholders(self):
        original = "Hello world"
        translated = "Hola {mundo} world"
        result = _fix_placeholders(original, translated)
        assert "{mundo}" not in result

    def test_fixes_renamed_placeholder(self):
        original = "Found {count} items"
        translated = "Encontrado {contagem} itens"
        result = _fix_placeholders(original, translated)
        assert "{count}" in result
        assert "{contagem}" not in result

    def test_removes_extra_when_more_than_original(self):
        original = "Hello %s"
        translated = "Olá %s %d"
        result = _fix_placeholders(original, translated)
        assert "%d" not in result
        assert "%s" in result

    def test_preserves_correct_translation(self):
        original = "{name} has {count} items"
        translated = "{name} tem {count} itens"
        result = _fix_placeholders(original, translated)
        assert result == "{name} tem {count} itens"

    def test_appends_missing(self):
        original = "Hello %s and %d"
        translated = "Olá %s e"
        result = _fix_placeholders(original, translated)
        assert "%d" in result


class TestCatalogWrites:
    def test_english_source_is_copied_without_api_calls(self, tmp_path):
        class NoCallAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate(self, *_args, **_kwargs):
                pytest.fail("English source must not call the API")

            def translate_batch(self, *_args, **_kwargs):
                pytest.fail("English source must not call the API")

        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.append(polib.POEntry(msgid="Open"))
        pot.append(
            polib.POEntry(
                msgid="{count} file",
                msgid_plural="{count} files",
                msgstr_plural={0: "", 1: ""},
            )
        )
        pot.save(str(pot_path))

        count = TranslationEngine(NoCallAPI(), "app").translate_language(
            pot_path, "en", tmp_path
        )

        translated = polib.pofile(str(tmp_path / "en.po"))
        assert count == 2
        assert translated[0].msgstr == "Open"
        assert translated[1].msgstr_plural == {
            0: "{count} file",
            1: "{count} files",
        }
        assert translated.metadata["Plural-Forms"] == (
            "nplurals=2; plural=(n != 1);"
        )

    def test_failed_atomic_save_preserves_existing_catalog(self, tmp_path):
        destination = tmp_path / "app.po"
        destination.write_text("original", encoding="utf-8")

        class BrokenCatalog:
            def save(self, path):
                Path(path).write_text("partial", encoding="utf-8")
                raise RuntimeError("write failed")

        with pytest.raises(RuntimeError, match="write failed"):
            _save_po_atomic(BrokenCatalog(), destination)

        assert destination.read_text(encoding="utf-8") == "original"
        assert not list(tmp_path.glob(".app.po.*.tmp"))

    def test_existing_catalog_symlink_is_replaced(self, tmp_path):
        class EchoAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate_batch(self, texts, *_args, **_kwargs):
                return [f"fr:{text}" for text in texts]

            def translate(self, text, *_args, **_kwargs):
                return f"fr:{text}"

        pot_path = tmp_path / "app.pot"
        catalog = polib.POFile()
        catalog.append(polib.POEntry(msgid="Open"))
        catalog.save(str(pot_path))
        victim = tmp_path / "victim.po"
        victim.write_text("do not change", encoding="utf-8")
        output = tmp_path / "fr.po"
        output.symlink_to(victim)

        TranslationEngine(EchoAPI(), "app").translate_language(
            pot_path,
            "fr",
            tmp_path,
        )

        assert not output.is_symlink()
        assert polib.pofile(str(output))[0].msgstr == "fr:Open"
        assert victim.read_text(encoding="utf-8") == "do not change"


class TestContextCache:
    class SequencedAPI:
        batch_delay = 0

        def __init__(self, outputs=None, fail=False):
            self.outputs = outputs or []
            self.fail = fail
            self.batch_calls = 0
            self.individual_calls = 0

        def set_context(self, *_args):
            pass

        def translate_batch(self, texts, *_args, **_kwargs):
            self.batch_calls += 1
            if self.fail:
                raise RuntimeError("batch unavailable")
            return self.outputs[: len(texts)]

        def translate(self, text, *_args, **_kwargs):
            self.individual_calls += 1
            if self.fail:
                raise RuntimeError("translation unavailable")
            return f"translated:{text}"

    @staticmethod
    def _write_catalog(path, entries):
        catalog = polib.POFile()
        for entry in entries:
            catalog.append(entry)
        catalog.save(str(path))

    def test_duplicate_msgids_with_distinct_contexts_are_checked(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        entries = [
            polib.POEntry(msgctxt="menu", msgid="Open"),
            polib.POEntry(msgctxt="verb", msgid="Open"),
        ]
        self._write_catalog(pot_path, entries)
        self._write_catalog(
            tmp_path / "fr.po",
            [
                polib.POEntry(msgctxt="menu", msgid="Open", msgstr="Menu old"),
                polib.POEntry(msgctxt="verb", msgid="Open", msgstr="Verb old"),
            ],
        )
        api = self.SequencedAPI(["Menu new", "Verb new"])

        result = TranslationEngine(api, "app").fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
        )

        translated = polib.pofile(str(tmp_path / "fr.po"))
        assert result == {"fr": True}
        assert api.batch_calls == 1
        assert [entry.msgstr for entry in translated] == [
            "Menu new",
            "Verb new",
        ]

    def test_failed_reference_check_is_reported_and_cached(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        self._write_catalog(pot_path, [polib.POEntry(msgid="Open")])
        self._write_catalog(
            tmp_path / "fr.po",
            [polib.POEntry(msgid="Open", msgstr="Ouvrir")],
        )

        result = TranslationEngine(
            self.SequencedAPI(fail=True),
            "app",
        ).fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
        )

        cache_path = tmp_path / ".langforge_context_cache.json"
        assert result == {"fr": False}
        assert cache_path.is_file()
        assert json.loads(cache_path.read_text(encoding="utf-8"))["checked"] == []

    def test_cancelled_check_preserves_cache_and_can_resume(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        self._write_catalog(pot_path, [polib.POEntry(msgid="Open")])
        self._write_catalog(
            tmp_path / "fr.po",
            [polib.POEntry(msgid="Open", msgstr="Old")],
        )
        cancel_event = threading.Event()
        cancel_event.set()

        cancelled = TranslationEngine(
            self.SequencedAPI(["New"]),
            "app",
        ).fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
            cancel_event=cancel_event,
        )

        cache_path = tmp_path / ".langforge_context_cache.json"
        assert cancelled == {}
        assert cache_path.is_file()

        api = self.SequencedAPI(["New"])
        resumed = TranslationEngine(api, "app").fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
        )

        assert resumed == {"fr": True}
        assert api.batch_calls == 1
        assert not cache_path.exists()

    def test_stale_cache_fingerprint_is_ignored(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        self._write_catalog(pot_path, [polib.POEntry(msgid="Open")])
        self._write_catalog(
            tmp_path / "fr.po",
            [polib.POEntry(msgid="Open", msgstr="Old")],
        )
        cache_path = tmp_path / ".langforge_context_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "fingerprint": "stale",
                    "checked": ['["","Open",""]'],
                    "changed": [],
                    "fixed_langs": [],
                }
            ),
            encoding="utf-8",
        )
        api = self.SequencedAPI(["New"])

        result = TranslationEngine(api, "app").fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
        )

        assert result == {"fr": True}
        assert api.batch_calls == 1

    def test_cache_symlink_is_replaced_without_touching_target(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        self._write_catalog(pot_path, [polib.POEntry(msgid="Open")])
        self._write_catalog(
            tmp_path / "fr.po",
            [polib.POEntry(msgid="Open", msgstr="Old")],
        )
        victim = tmp_path / "victim.json"
        victim.write_text("keep", encoding="utf-8")
        cache_path = tmp_path / ".langforge_context_cache.json"
        cache_path.symlink_to(victim)
        cancel_event = threading.Event()
        cancel_event.set()

        TranslationEngine(self.SequencedAPI(["New"]), "app").fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
            cancel_event=cancel_event,
        )

        assert not cache_path.is_symlink()
        assert json.loads(cache_path.read_text(encoding="utf-8"))["version"] == 2
        assert victim.read_text(encoding="utf-8") == "keep"

    @pytest.mark.parametrize("method", ["translate_language", "fix_context"])
    def test_template_symlink_is_rejected(self, tmp_path, method):
        victim = tmp_path / "victim.pot"
        self._write_catalog(victim, [polib.POEntry(msgid="Open")])
        pot_path = tmp_path / "app.pot"
        pot_path.symlink_to(victim)
        engine = TranslationEngine(self.SequencedAPI(["New"]), "app")

        with pytest.raises(ValueError, match="cannot be a symlink"):
            if method == "translate_language":
                engine.translate_language(pot_path, "fr", tmp_path)
            else:
                engine.fix_context(
                    pot_path,
                    tmp_path,
                    reference_lang="fr",
                    languages=["fr"],
                )
