"""Tests for core.translator placeholder protection and validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "usr" / "share" / "langforge"))

from core.translator import (
    _protect_placeholders,
    _restore_placeholders,
    _validate_placeholders,
    _fix_placeholders,
)
from api.base import build_translation_prompt, TranslationAPI


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

    def test_valid_reordered(self):
        # Same placeholders but in different order — still valid (sorted comparison)
        assert _validate_placeholders(
            "%s has %d items", "%d itens de %s"
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
        assert "en" in prompt
        assert "pt-BR" in prompt

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
