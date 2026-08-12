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
    _is_translation_plausible,
    _match_boundary_whitespace,
    _validate_translation_integrity,
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
        protected, tokens = _protect_placeholders("{{literal}} and {value:{width}.2f}")

        assert tokens == [("<x1/>", "{value:{width}.2f}")]
        assert protected == "{{literal}} and <x1/>"

    def test_empty_string(self):
        text = ""
        protected, tokens = _protect_placeholders(text)
        assert protected == ""
        assert len(tokens) == 0

    def test_subtitle_italics_are_protected(self):
        protected, tokens = _protect_placeholders("<i>Whispered line</i>")

        assert protected == "<x1/>Whispered line<x2/>"
        assert [placeholder for _, placeholder in tokens] == ["<i>", "</i>"]

    def test_markup_with_attributes_is_one_token(self):
        text = '<font color="#ffffff">Hi %s</font>'
        protected, tokens = _protect_placeholders(text)

        assert [placeholder for _, placeholder in tokens] == [
            '<font color="#ffffff">',
            "%s",
            "</font>",
        ]
        assert _restore_placeholders(protected, tokens) == text

    def test_comparison_signs_are_not_markup(self):
        text = "Use a < b and c > d"
        protected, tokens = _protect_placeholders(text)

        assert protected == text
        assert tokens == []


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


class TestMangledTokenRecovery:
    """A model that rewrites <xN/> must not cost us the translation."""

    @pytest.mark.parametrize(
        "mangled",
        [
            "<x1/>Olá<x2/>",
            "<X1/>Olá<X2/>",
            "< x1 />Olá< x2/>",
            "&lt;x1/&gt;Olá&lt;x2/&gt;",
            "&lt;X1 /&gt;Olá< X2 >",
            "<x1>Olá[x2]",
        ],
    )
    def test_italics_are_recovered_and_validated(self, mangled):
        original = "<i>Hello</i>"
        _protected, tokens = _protect_placeholders(original)

        restored = _restore_placeholders(mangled, tokens)

        assert restored == "<i>Olá</i>"
        assert _validate_translation_integrity(original, restored)

    def test_two_digit_tokens_are_not_confused(self):
        tokens = [("<x1/>", "<i>"), ("<x12/>", "<b>")]

        restored = _restore_placeholders("<x12/>a<x1/>b", tokens)

        assert restored == "<b>a<i>b"


class TestLocalizedFreedom:
    """Rules that used to reject correct translations of real catalogs."""

    def test_ampersand_entity_may_be_dropped(self):
        # Korean joins with 및 and has no use for the character itself.
        assert _validate_translation_integrity(
            "Application &amp; Window", "애플리케이션 및 창"
        )

    def test_other_entities_must_survive(self):
        assert not _validate_translation_integrity("Use &lt;name&gt;", "Utiliser nom")

    def test_no_entity_may_be_invented(self):
        assert not _validate_translation_integrity(
            "Application Window", "Aplicação &amp; Janela"
        )

    def test_hyphen_compound_keeps_the_flag_intact(self):
        # German writes "(--user-Flag)"; the flag is still --user.
        assert _validate_translation_integrity(
            "Operate on user services (--user flag)",
            "Auf Benutzerdienste anwenden (--user-Flag)",
        )

    def test_a_word_that_looks_like_a_command_is_allowed(self):
        # "pip" is Norwegian for beep, not the package manager.
        assert _validate_translation_integrity("Double Beep", "Dobbelt pip")

    def test_a_source_command_may_not_be_duplicated(self):
        assert not _validate_translation_integrity(
            "Run git commit", "Exécuter git commit git commit"
        )

    def test_a_number_may_be_spelled_out_when_the_source_has_none(self):
        # Korean renders "Last Hour" as "지난 1시간".
        assert _validate_translation_integrity("Last Hour", "지난 1시간")

    def test_a_word_number_does_not_lock_the_digits(self):
        # "One or more hex codes" has no digit; Korean writes hex as 16진수.
        assert _validate_translation_integrity(
            "One or more hex codes are invalid.",
            "하나 이상의 16진수 코드가 잘못되었습니다.",
        )

    def test_a_lowercase_hyphen_compound_keeps_the_flag(self):
        # Norwegian writes "(--user-flagg)"; the flag is still --user.
        assert _validate_translation_integrity(
            "Operate on user services (--user flag)",
            "Operer på brukertjenester (--user-flagg)",
        )

    def test_a_different_flag_is_still_rejected(self):
        assert not _validate_translation_integrity(
            "Operate on user services (--user flag)",
            "Operer på brukertjenester (--system-flagg)",
        )

    def test_the_dialogue_dash_is_not_a_command_flag(self):
        # A subtitle opens a speaker line with "-I"; no translation carries it.
        assert _validate_translation_integrity(
            "-I hope you keep that in mind.\n-I'll keep that in mind.",
            "-Espero que você leve isso em conta.\n-Vou levar em conta.",
        )

    def test_a_dialogue_dash_may_become_an_em_dash(self):
        assert _validate_translation_integrity(
            "-Wipe the drool.\n-I think I'm falling in love.",
            "—Enxuga a baba.\n—Acho que estou me apaixonando.",
        )

    def test_a_real_command_flag_is_still_required(self):
        assert _validate_translation_integrity("Run git add -A", "Exécuter git add -A")
        assert not _validate_translation_integrity("Run git add -A", "Exécuter git add")
        assert not _validate_translation_integrity(
            "Use -v for verbose output", "Use -x para saída detalhada"
        )

    def test_a_source_number_still_may_not_change(self):
        assert not _validate_translation_integrity(
            "Delete 3 files", "Excluir 5 arquivos"
        )


class TestScaledNumbers:
    """A scaled quantity is respelled per locale and cannot be compared."""

    @pytest.mark.parametrize(
        "translated",
        ["500.000 tegn/måned", "500 000 tecken/månad", "月間50万文字"],
    )
    def test_scaled_source_accepts_localized_expansion(self, translated):
        assert _validate_translation_integrity("500k characters/month", translated)

    def test_plain_numbers_are_still_compared(self):
        assert _validate_translation_integrity("Delete 3 files", "Excluir 3 arquivos")
        assert not _validate_translation_integrity(
            "Delete 3 files", "Excluir 5 arquivos"
        )


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


class TestBuildTranslationPrompt:
    def test_includes_app_name(self):
        prompt = build_translation_prompt("en", "pt-BR", app_name="ashy-term")
        assert "ashy-term" in prompt

    def test_app_name_in_do_not_translate_rule(self):
        prompt = build_translation_prompt("en", "pt-BR", app_name="ashy-term")
        assert "gettext textdomain is 'ashy-term'" in prompt
        assert "not automatically a display name" in prompt

    def test_trusted_item_instruction_is_separate_from_ui_samples(self):
        prompt = build_translation_prompt(
            "en",
            "ru",
            context_entries=["Open", "Close"],
            item_instruction="Use gettext plural form 2 for example count 5.",
        )

        assert "other UI strings" in prompt
        assert "Additional requirement for this item" in prompt
        assert "plural form 2" in prompt

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
        assert "my_cool_app" in prompt


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


class TestTranslationIntegrity:
    def test_restores_exact_boundary_whitespace(self):
        assert _match_boundary_whitespace(" \nHello\t", "  Olá  ") == " \nOlá\t"

    @pytest.mark.parametrize(
        ("source", "translation"),
        [
            ("First line\nSecond line", "Première ligne<br>Deuxième ligne"),
            ("Open GitHub README.md", "Ouvrir GitLab LISEZ-MOI.md"),
            ("Run git commit --amend", "Exécuter git confirmer --modifier"),
            ("Retry in 30 seconds", "Réessayer dans 3 secondes"),
            ("Retry in 30 seconds", "Réessayer dans 300 secondes"),
            ("Wait 30s", "Attendre 3s"),
            ("gtk4", "gtk5"),
            ("Value -10", "Valeur 10"),
            ("Progress 30%", "Progression 30"),
            ("30 files/10 seconds", "10 fichiers/30 secondes"),
            ("Choose one of 2 options", "Choisir parmi 2 options, numéro 1"),
            ("less than 1 min", "moins de 2 min"),
            ("3. Name", "4. Nom"),
            ("Use https://example.com", "Utiliser https://example.com.evil"),
            ("Use /usr/bin/git", "Utiliser /usr/bin/gitte"),
            ("Run git commit", "Exécuter git commit git commit"),
            ("Run git credential reject", "Exécuter git credential rejeter"),
            ("Run sudo pacman -Syu", "Exécuter pacman -Syu"),
            ("Run git add -A", "Exécuter git add -Afoo"),
            ("Open settings", "Ouvrir {param"),
            ("Hello {name}", "Bonjour {name} {broken"),
            ("Select %1", "Sélectionnez %2"),
            ("Use $1 and $HOME", "Utiliser $2 et $ACCUEIL"),
            ("Value ${HOME}", "Valeur {HOME}"),
            ("Value %s", "Valeur %s %BROKEN"),
            ("Tom &amp; Jerry", "Tom &amp; Jerry &amp; Titi"),
            ("Use &lt;name&gt;", "Utiliser nom"),
            ("[{0}/{1}] {2}", "[13] {0}/{1} {2}"),
            ("Open settings", "Ouvrir\x00 paramètres"),
            ("Open settings", "Ouvrir\x01 paramètres"),
            ("Open settings", "Ouvrir\tparamètres"),
            ("Open settings", "Ouvrir\rparamètres"),
            ("Safe source", "Traduction |||NEXT||"),
            ("Safe source", "Traduction <x2>"),
            ("Safe source", "Traduction [13]"),
        ],
    )
    def test_rejects_structural_corruption(self, source, translation):
        assert not _validate_translation_integrity(source, translation)

    def test_accepts_localized_grammar_around_protected_terms(self):
        assert _validate_translation_integrity(
            "Commit reached origin with GitHub cache",
            "Commit saavutti origin-haaran GitHub-välimuistin",
        )

    def test_accepts_localized_number_separators_and_sentence_punctuation(self):
        assert _validate_translation_integrity(
            "Visit https://example.com. Version 1.5, total 1,000.",
            "https://example.com を参照。バージョン 1,5、合計 1.000。",
        )

    def test_accepts_source_number_word_rendered_as_a_digit(self):
        assert _validate_translation_integrity(
            "Container must produce exactly one ISO file",
            "Le conteneur doit produire exactement 1 fichier ISO",
        )

    def test_literal_boundaries_do_not_match_inside_words(self):
        assert _validate_translation_integrity(
            "SUBMIT PROFILE MESSAGES",
            "ENVIAR PERFIL MENSAGENS",
        )

    def test_brand_identity_is_case_insensitive_but_not_interchangeable(self):
        assert not _validate_translation_integrity(
            "Open github",
            "Ouvrir gitlab",
        )

    def test_brand_prefix_does_not_accept_camel_case_extension(self):
        assert not _validate_translation_integrity(
            "Open GitHub",
            "Ouvrir GitHubEvil",
        )

    def test_brand_root_allows_grammatical_suffix(self):
        assert _validate_translation_integrity(
            "Open GitHub",
            "Otevřít GitHubu",
        )

    def test_indexed_qt_placeholders_may_reorder(self):
        assert _validate_translation_integrity(
            "Copy %1. Then %L2.",
            "Dann %L2 kopieren. Danach %1.",
        )

    @pytest.mark.parametrize(
        ("source", "translation"),
        [
            ("_File", "F_ichier"),
            ("&Open", "O&uvrir"),
        ],
    )
    def test_accelerators_may_move(self, source, translation):
        assert _validate_translation_integrity(source, translation)

    def test_internal_accelerator_cannot_disappear(self):
        assert not _validate_translation_integrity("E_xit", "Quitter")
        assert _validate_translation_integrity("E_xit", "Q_uitter")

    def test_unconfigured_textdomain_is_not_treated_as_a_brand(self):
        assert _validate_translation_integrity(
            "Open the app",
            "Ouvrir l’application",
        )

    def test_explicit_display_name_is_preserved(self):
        assert not _validate_translation_integrity(
            "Open my-app",
            "Ouvrir Mon Appli",
            "my-app",
        )


class TestTranslationPlausibility:
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

    def test_stripped_tags_are_not_guessed(self):
        text = "Olá x1 mundo"
        tokens = [("<x1/>", "%s")]
        assert _restore_placeholders(text, tokens) == text

    def test_unknown_residual_xml_is_preserved_for_validation(self):
        text = "Olá <x99/> mundo"
        assert _restore_placeholders(text, []) == text

    def test_multiple_corruptions(self):
        text = "A <X1/> B <x2 /> C"
        tokens = [("<x1/>", "%s"), ("<x2/>", "%d")]
        result = _restore_placeholders(text, tokens)
        assert result == "A %s B %d C"

    def test_overlapping_qt_and_printf_tokens_round_trip(self):
        text = "Value %1.2f, Qt %1 and %L2, named %(value)s"
        protected, tokens = _protect_placeholders(text)

        assert _restore_placeholders(protected, tokens) == text
        assert [placeholder for _token, placeholder in tokens] == [
            "%1.2f",
            "%1",
            "%L2",
            "%(value)s",
        ]


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
        assert translated.metadata["Plural-Forms"] == ("nplurals=2; plural=(n != 1);")

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

    def test_project_merge_removes_obsolete_entries(self, tmp_path):
        class NoCallAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate(self, *_args, **_kwargs):
                pytest.fail("No translation should be needed")

            def translate_batch(self, *_args, **_kwargs):
                pytest.fail("No translation should be needed")

        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.metadata = {"Project-Id-Version": "Example 1.0"}
        pot.append(polib.POEntry(msgid="Open"))
        pot.save(str(pot_path))
        existing = polib.POFile()
        existing.append(polib.POEntry(msgid="Open", msgstr="Ouvrir"))
        existing.append(polib.POEntry(msgid="Removed", msgstr="Ancien", obsolete=True))
        existing.save(str(tmp_path / "fr.po"))

        TranslationEngine(NoCallAPI(), "app").translate_language(
            pot_path, "fr", tmp_path
        )

        translated = polib.pofile(str(tmp_path / "fr.po"))
        assert not translated.obsolete_entries()
        assert translated.metadata["Project-Id-Version"] == "Example 1.0"
        assert translated.metadata["X-Generator"] == "LangForge"

    def test_project_merge_copies_empty_template_metadata(self, tmp_path):
        class NoCallAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate(self, *_args, **_kwargs):
                pytest.fail("No translation should be needed")

            def translate_batch(self, *_args, **_kwargs):
                pytest.fail("No translation should be needed")

        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.metadata = {"Report-Msgid-Bugs-To": ""}
        pot.append(polib.POEntry(msgid="Open"))
        pot.save(str(pot_path))
        existing = polib.POFile()
        existing.metadata = {"Report-Msgid-Bugs-To": "old@example.test"}
        existing.append(polib.POEntry(msgid="Open", msgstr="Ouvrir"))
        existing.save(str(tmp_path / "fr.po"))

        TranslationEngine(NoCallAPI(), "app").translate_language(
            pot_path, "fr", tmp_path
        )

        translated = polib.pofile(str(tmp_path / "fr.po"))
        assert translated.metadata["Report-Msgid-Bugs-To"] == ""

    def test_existing_corrupt_translation_is_flagged_not_overwritten(self, tmp_path):
        class NoCallAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate(self, *_args, **_kwargs):
                pytest.fail("Existing non-fuzzy entries are audited, not translated")

            def translate_batch(self, *_args, **_kwargs):
                pytest.fail("Existing non-fuzzy entries are audited, not translated")

        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.append(polib.POEntry(msgid="Open settings"))
        pot.save(str(pot_path))
        existing = polib.POFile()
        existing.append(
            polib.POEntry(msgid="Open settings", msgstr="Ouvrir <br> paramètres")
        )
        existing.save(str(tmp_path / "fr.po"))
        engine = TranslationEngine(NoCallAPI(), "app")

        count = engine.translate_language(pot_path, "fr", tmp_path)

        entry = polib.pofile(str(tmp_path / "fr.po"))[0]
        assert count == 0
        # The run never asked for this entry, so its text survives: it is only
        # flagged, which makes the next run retranslate it.
        assert entry.msgstr == "Ouvrir <br> paramètres"
        assert "fuzzy" in entry.flags
        # A stale entry from an earlier run must not fail this one.
        assert engine.last_language_complete is True

    def test_stale_entries_do_not_fail_a_run_that_translated_everything(self, tmp_path):
        """One old broken form must not report the whole language as an error."""

        class BatchAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate(self, text, _source, target):
                return f"{target}:{text}"

            def translate_batch(self, texts, _source, target):
                return [f"{target}:{text}" for text in texts]

        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.append(polib.POEntry(msgid="Fresh string"))
        pot.append(polib.POEntry(msgid="Old\nwrapped\nstring"))
        pot.save(str(pot_path))
        existing = polib.POFile()
        existing.append(
            polib.POEntry(msgid="Old\nwrapped\nstring", msgstr="Colapsada numa linha")
        )
        existing.save(str(tmp_path / "fr.po"))
        engine = TranslationEngine(BatchAPI(), "app")

        engine.translate_language(pot_path, "fr", tmp_path)

        catalog = polib.pofile(str(tmp_path / "fr.po"))
        stale = catalog.find("Old\nwrapped\nstring")
        assert stale.msgstr == "Colapsada numa linha"
        assert "fuzzy" in stale.flags
        assert catalog.find("Fresh string").msgstr == "fr:Fresh string"
        assert engine.last_language_complete is True

    def test_one_pending_entry_is_reported_as_partial_not_error(self, tmp_path):
        """A catalog that is 1 of 2 short is partial: it self-heals next run."""

        class HalfBrokenAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def _render(self, text, target):
                # The model drops a line break on one string, nothing else.
                if "\n" in text:
                    return "uma linha so"
                return f"{target}:{text}"

            def translate(self, text, _source, target):
                return self._render(text, target)

            def translate_batch(self, texts, _source, target):
                return [self._render(text, target) for text in texts]

        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.append(polib.POEntry(msgid="Open settings"))
        pot.append(polib.POEntry(msgid="First line\nsecond line"))
        pot.save(str(pot_path))
        statuses = []
        engine = TranslationEngine(HalfBrokenAPI(), "app")

        results = engine.translate_project(
            pot_path,
            tmp_path,
            progress_callback=lambda lang, status, *_: statuses.append(status),
            languages=["fr"],
        )

        assert results == {"fr": False}
        assert statuses[-1] == "partial: 1 strings pending"
        assert "error" not in statuses[-1]
        assert engine.last_language_pending == 1

    def test_textdomain_name_is_audited_only_as_project_context(self, tmp_path):
        class NoCallAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate(self, *_args, **_kwargs):
                pytest.fail("Existing entry should not call the API")

            def translate_batch(self, *_args, **_kwargs):
                pytest.fail("Existing entry should not call the API")

        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.append(polib.POEntry(msgid="Open the app"))
        pot.save(str(pot_path))
        existing = polib.POFile()
        existing.append(
            polib.POEntry(msgid="Open the app", msgstr="Ouvrir l’application")
        )
        existing.save(str(tmp_path / "fr.po"))
        engine = TranslationEngine(NoCallAPI(), "app")

        engine.translate_language(pot_path, "fr", tmp_path)

        entry = polib.pofile(str(tmp_path / "fr.po"))[0]
        assert entry.msgstr == "Ouvrir l’application"
        assert "fuzzy" not in entry.flags
        assert engine.last_language_complete is True


class TestContextCache:
    class SequencedAPI:
        batch_delay = 0
        supports_context = True

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

    def test_fix_context_requires_explicit_provider_capability(self, tmp_path):
        api = self.SequencedAPI(["New"])
        api.supports_context = False

        with pytest.raises(RuntimeError, match="does not support"):
            TranslationEngine(api, "app").fix_context(
                tmp_path / "app.pot",
                tmp_path,
                reference_lang="fr",
                languages=["fr"],
            )

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

    def test_reference_catalog_is_merged_and_canonicalized(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        pot = polib.POFile()
        pot.metadata = {
            "Project-Id-Version": "Example 2.0",
            "Report-Msgid-Bugs-To": "",
        }
        pot.append(polib.POEntry(msgid="Open"))
        pot.append(polib.POEntry(msgid="Added"))
        pot.save(str(pot_path))
        reference = polib.POFile()
        reference.metadata = {
            "Project-Id-Version": "Example 1.0",
            "Report-Msgid-Bugs-To": "old@example.test",
        }
        reference.append(polib.POEntry(msgid="Open", msgstr="Ouvrir"))
        reference.append(polib.POEntry(msgid="Removed", msgstr="Ancien", obsolete=True))
        reference.save(str(tmp_path / "fr.po"))

        result = TranslationEngine(
            self.SequencedAPI(["Ouvrir", "Ajouté"]),
            "app",
        ).fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
        )

        translated = polib.pofile(str(tmp_path / "fr.po"))
        assert result == {"fr": True}
        assert translated.find("Added").msgstr == "Ajouté"
        assert not translated.obsolete_entries()
        assert translated.metadata["Project-Id-Version"] == "Example 2.0"
        assert translated.metadata["Report-Msgid-Bugs-To"] == ""
        assert translated.metadata["X-Generator"] == "LangForge"

    def test_reference_context_fix_restores_exact_boundary_whitespace(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        self._write_catalog(pot_path, [polib.POEntry(msgid=" Open ")])
        self._write_catalog(
            tmp_path / "fr.po",
            [polib.POEntry(msgid=" Open ", msgstr="Ouvrir")],
        )

        result = TranslationEngine(
            self.SequencedAPI(["Ouvrir"]),
            "app",
        ).fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
        )

        entry = polib.pofile(str(tmp_path / "fr.po"))[0]
        assert result == {"fr": True}
        assert entry.msgstr == " Ouvrir "

    def test_reference_language_is_included_in_workflow_progress(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        self._write_catalog(pot_path, [polib.POEntry(msgid="Open")])
        self._write_catalog(
            tmp_path / "fr.po",
            [polib.POEntry(msgid="Open", msgstr="Ancien")],
        )
        self._write_catalog(
            tmp_path / "de.po",
            [polib.POEntry(msgid="Open", msgstr="Alt")],
        )
        progress = []

        result = TranslationEngine(
            self.SequencedAPI(["Nouveau"]),
            "app",
        ).fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["de"],
            progress_callback=lambda lang, status, current, total: progress.append(
                (lang, status, current, total)
            ),
        )

        assert result == {"fr": True, "de": True}
        assert {item[0] for item in progress} == {"fr", "de"}
        language_progress = [
            (lang, current, total)
            for lang, status, current, total in progress
            if status == "success: reference language"
            or status.startswith("success: fixed")
        ]
        assert language_progress == [("fr", 1, 2), ("de", 2, 2)]

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
                    "version": 3,
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

    def test_legacy_policy_cache_is_ignored(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        self._write_catalog(pot_path, [polib.POEntry(msgid="Open")])
        self._write_catalog(
            tmp_path / "fr.po",
            [polib.POEntry(msgid="Open", msgstr="Old")],
        )
        cancel_event = threading.Event()
        cancel_event.set()
        TranslationEngine(self.SequencedAPI(["New"]), "app").fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
            cancel_event=cancel_event,
        )
        cache_path = tmp_path / ".langforge_context_cache.json"
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        cache["version"] = 2
        cache["checked"] = ['["","Open",""]']
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
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
        assert json.loads(cache_path.read_text(encoding="utf-8"))["version"] == 3
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
