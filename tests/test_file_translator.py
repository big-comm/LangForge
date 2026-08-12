"""Tests for structure-preserving standalone file translation."""

import json
import stat
import threading
from pathlib import Path

import polib
import pytest

from core import file_translator
from core.file_translator import (
    FileTranslator,
    _file_output_path,
    _po_output_path,
    _write_atomic,
)
from core.languages import get_file_lang_code, get_plural_rule


class EchoTranslationAPI:
    """Deterministic translation API for file tests."""

    batch_delay = 0

    def __init__(self):
        self.batches: list[list[str]] = []

    def set_context(self, app_name, context_entries):
        self.context = (app_name, context_entries)

    def translate_batch(self, texts, source_lang, target_lang):
        self.batches.append(list(texts))
        return [f"{target_lang}:{text}" for text in texts]


def _translation_fixture(tmp_path, suffix):
    source = tmp_path / f"sample{suffix}"
    if suffix == ".po":
        catalog = polib.POFile()
        catalog.append(polib.POEntry(msgid="Hello"))
        catalog.save(str(source))
        output = tmp_path / "sample.fr.po"
    elif suffix == ".json":
        source.write_text(
            json.dumps({"title": "Hello", "subtitle": "World"}),
            encoding="utf-8",
        )
        output = _file_output_path(source, "fr", suffix)
    elif suffix in {".txt", ".md"}:
        source.write_text("Hello\n\nWorld", encoding="utf-8")
        output = _file_output_path(source, "fr", suffix)
    elif suffix == ".srt":
        source.write_text(
            "1\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "Hello\n\n"
            "2\n"
            "00:00:03,000 --> 00:00:04,000\n"
            "World\n",
            encoding="utf-8",
        )
        output = _file_output_path(source, "fr", suffix)
    else:
        raise AssertionError(f"Unhandled fixture suffix: {suffix}")
    return source, output


def _assert_translated_output(path, suffix):
    if suffix == ".po":
        assert polib.pofile(str(path))[0].msgstr == "fr:Hello"
    elif suffix == ".json":
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "title": "fr:Hello",
            "subtitle": "fr:World",
        }
    else:
        content = path.read_text(encoding="utf-8")
        assert "fr:Hello" in content
        assert "fr:World" in content


def test_pot_translation_preserves_catalog_structure(tmp_path):
    source = tmp_path / "messages.pot"
    catalog = polib.POFile()
    catalog.metadata = {
        "Project-Id-Version": "example 1.0",
        "Report-Msgid-Bugs-To": "bugs@example.test",
        "Content-Type": "text/plain; charset=CHARSET",
        "X-Custom-Metadata": "preserve-me",
    }
    catalog.metadata_is_fuzzy = True
    catalog.append(
        polib.POEntry(
            msgctxt="menu-action",
            msgid="Open",
            comment="Developer comment",
            tcomment="Translator comment",
            occurrences=[("src/menu.py", "42")],
            flags=["python-format", "fuzzy"],
            previous_msgctxt="old-menu-action",
            previous_msgid="Launch",
        )
    )
    catalog.append(
        polib.POEntry(
            msgctxt="file-count",
            msgid="One file",
            msgid_plural="Many files",
            msgstr_plural={0: "", 1: "", 2: ""},
            comment="Plural developer comment",
            tcomment="Plural translator comment",
            occurrences=[("src/files.py", "7")],
            flags=["python-brace-format"],
        )
    )
    catalog.append(
        polib.POEntry(
            msgid="Obsolete",
            msgstr="Old translation",
            comment="Keep obsolete metadata",
            obsolete=True,
        )
    )
    catalog.save(str(source))
    source_before = source.read_bytes()

    result = FileTranslator(EchoTranslationAPI(), source).translate_all(["fr"])

    output = tmp_path / "messages.fr.po"
    assert result == {"fr": True}
    assert output.exists()
    assert not (tmp_path / "messages.fr.pot").exists()
    assert source.read_bytes() == source_before

    translated = polib.pofile(str(output))
    assert translated.metadata["Project-Id-Version"] == "example 1.0"
    assert translated.metadata["Report-Msgid-Bugs-To"] == "bugs@example.test"
    assert translated.metadata["X-Custom-Metadata"] == "preserve-me"
    assert translated.metadata["Language"] == "fr"
    assert translated.metadata["Content-Type"] == "text/plain; charset=UTF-8"
    assert translated.metadata["Plural-Forms"] == get_plural_rule("fr").header
    assert translated.metadata_is_fuzzy == []

    singular = next(e for e in translated if e.msgctxt == "menu-action")
    assert singular.msgstr == "fr:Open"
    assert singular.comment == "Developer comment"
    assert singular.tcomment == "Translator comment"
    assert singular.occurrences == [("src/menu.py", "42")]
    assert singular.flags == ["python-format"]
    assert singular.previous_msgctxt == "old-menu-action"
    assert singular.previous_msgid == "Launch"

    plural = next(e for e in translated if e.msgctxt == "file-count")
    assert plural.msgid == "One file"
    assert plural.msgid_plural == "Many files"
    assert plural.msgstr_plural == {
        0: "fr:One file",
        1: "fr:Many files",
    }
    assert plural.comment == "Plural developer comment"
    assert plural.tcomment == "Plural translator comment"
    assert plural.occurrences == [("src/files.py", "7")]
    assert plural.flags == ["python-brace-format"]

    obsolete = next(e for e in translated if e.obsolete)
    assert obsolete.msgid == "Obsolete"
    assert obsolete.msgstr == "Old translation"


def test_portuguese_variants_use_distinct_output_files(tmp_path):
    source = tmp_path / "labels.json"
    source.write_text(json.dumps({"title": "Hello"}), encoding="utf-8")

    result = FileTranslator(EchoTranslationAPI(), source).translate_all(["pt-BR", "pt"])

    brazilian = tmp_path / "labels.por-BR.json"
    portuguese = tmp_path / "labels.por.json"
    assert result == {"pt-BR": True, "pt": True}
    assert get_file_lang_code("pt-BR") != get_file_lang_code("pt")
    assert json.loads(brazilian.read_text(encoding="utf-8")) == {"title": "pt-BR:Hello"}
    assert json.loads(portuguese.read_text(encoding="utf-8")) == {"title": "pt:Hello"}


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("ja", {0: "ja:Many files"}),
        (
            "ru",
            {
                0: "ru:One file",
                1: "ru:Many files",
                2: "ru:Many files",
            },
        ),
    ],
)
def test_po_uses_target_plural_cardinality(tmp_path, lang, expected):
    source = tmp_path / "messages.pot"
    catalog = polib.POFile()
    catalog.append(
        polib.POEntry(
            msgid="One file",
            msgid_plural="Many files",
            msgstr_plural={},
        )
    )
    catalog.save(str(source))

    result = FileTranslator(EchoTranslationAPI(), source).translate_all([lang])

    translated = polib.pofile(str(tmp_path / f"messages.{lang}.po"))
    assert result == {lang: True}
    assert translated[0].msgstr_plural == expected
    assert translated.metadata["Plural-Forms"] == get_plural_rule(lang).header


def test_po_clears_fuzzy_only_for_successful_entries(tmp_path):
    class PartialFailureAPI(EchoTranslationAPI):
        def translate_batch(self, texts, source_lang, target_lang):
            raise RuntimeError("batch failed")

        def translate(self, text, source_lang, target_lang):
            if text == "World":
                raise RuntimeError("item failed")
            return f"{target_lang}:{text}"

    source = tmp_path / "messages.pot"
    catalog = polib.POFile()
    catalog.metadata_is_fuzzy = True
    catalog.append(polib.POEntry(msgid="Hello", flags=["fuzzy"]))
    catalog.append(polib.POEntry(msgid="World", flags=["fuzzy"]))
    catalog.save(str(source))

    result = FileTranslator(PartialFailureAPI(), source).translate_all(["fr"])

    translated = polib.pofile(str(tmp_path / "messages.fr.po"))
    assert result == {"fr": False}
    assert translated.metadata_is_fuzzy == ["fuzzy"]
    assert translated[0].msgstr == "fr:Hello"
    assert "fuzzy" not in translated[0].flags
    assert translated[1].msgstr == "World"
    assert "fuzzy" in translated[1].flags


def test_truncated_batch_retries_every_item_through_strict_protocol(tmp_path):
    class TruncatedAPI(EchoTranslationAPI):
        def __init__(self):
            super().__init__()
            self.individual_calls = []

        def translate_batch(self, texts, source_lang, target_lang):
            self.batches.append(list(texts))
            return [f"batch:{texts[0]}"]

        def translate(self, value, source, target, /):
            self.individual_calls.append(value)
            return f"single:{value}"

    source = tmp_path / "labels.json"
    source.write_text(
        json.dumps({"first": "Hello", "second": "World"}),
        encoding="utf-8",
    )
    api = TruncatedAPI()

    result = FileTranslator(api, source).translate_all(["fr"])

    output = _file_output_path(source, "fr", ".json")
    assert result == {"fr": True}
    # Each item is retried alone as a one-item batch: the free-form prompt is
    # never used, so the model cannot answer with prose instead of a string.
    assert api.individual_calls == []
    assert api.batches[-2:] == [["Hello"], ["World"]]
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "first": "batch:Hello",
        "second": "batch:World",
    }


def test_individual_failure_marks_partial_output_failed(tmp_path):
    class FailingAPI(EchoTranslationAPI):
        def translate_batch(self, texts, source_lang, target_lang):
            raise RuntimeError("batch failed")

        def translate(self, text, source_lang, target_lang):
            if text == "World":
                raise RuntimeError("item failed")
            return f"single:{text}"

    source = tmp_path / "labels.json"
    source.write_text(
        json.dumps({"first": "Hello", "second": "World"}),
        encoding="utf-8",
    )

    result = FileTranslator(FailingAPI(), source).translate_all(["fr"])

    output = _file_output_path(source, "fr", ".json")
    assert result == {"fr": False}
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "first": "single:Hello",
        "second": "World",
    }


def test_nested_json_strings_are_translated_without_flattening(tmp_path):
    source = tmp_path / "labels.json"
    source.write_text(
        json.dumps(
            {
                "menu": {"open": "Open", "items": ["First", 2, None]},
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )

    result = FileTranslator(EchoTranslationAPI(), source).translate_all(["fr"])

    output = _file_output_path(source, "fr", ".json")
    assert result == {"fr": True}
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "menu": {"open": "fr:Open", "items": ["fr:First", 2, None]},
        "enabled": True,
    }


def test_text_translation_preserves_exact_whitespace_and_crlf(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"  First  \r\n\r\n\r\n\tSecond\t\r\n")

    result = FileTranslator(EchoTranslationAPI(), source).translate_all(["fr"])

    output = _file_output_path(source, "fr", ".txt")
    assert result == {"fr": True}
    assert output.read_bytes() == b"  fr:First  \r\n\r\n\r\n\tfr:Second\t\r\n"


def test_markdown_fenced_code_is_not_translated(tmp_path):
    source = tmp_path / "guide.md"
    source.write_text(
        "Introduction\n\n"
        "```python\n"
        'print("Do not translate")\n\n'
        "```not-a-close\n"
        'label = _("Still code")\n'
        "```\n\n"
        "Conclusion\n",
        encoding="utf-8",
    )
    api = EchoTranslationAPI()

    result = FileTranslator(api, source).translate_all(["fr"])

    output = _file_output_path(source, "fr", ".md")
    assert result == {"fr": True}
    assert api.batches == [["Introduction", "Conclusion"]]
    assert output.read_text(encoding="utf-8") == (
        "fr:Introduction\n\n"
        "```python\n"
        'print("Do not translate")\n\n'
        "```not-a-close\n"
        'label = _("Still code")\n'
        "```\n\n"
        "fr:Conclusion\n"
    )


def test_existing_target_suffix_never_overwrites_source(tmp_path):
    source = tmp_path / "labels.por.json"
    original = json.dumps({"title": "Hello"})
    source.write_text(original, encoding="utf-8")

    output = _file_output_path(source, "pt", ".json")
    result = FileTranslator(EchoTranslationAPI(), source).translate_all(["pt"])

    assert output == tmp_path / "labels.por.por.json"
    assert output != source
    assert result == {"pt": True}
    assert source.read_text(encoding="utf-8") == original
    assert json.loads(output.read_text(encoding="utf-8")) == {"title": "pt:Hello"}


def test_text_output_rejects_target_language_path_traversal(tmp_path):
    source = tmp_path / "document.txt"
    source.write_text("Hello", encoding="utf-8")
    (tmp_path / "document.x").mkdir()

    with pytest.raises(ValueError, match="Invalid target language code"):
        _file_output_path(source, "x/../../escaped", ".txt")

    assert not (tmp_path.parent / "escaped.txt").exists()


def test_po_output_rejects_target_language_path_traversal(tmp_path):
    source = tmp_path / "messages.po"
    source.write_text('msgid "Hello"\nmsgstr ""\n', encoding="utf-8")
    (tmp_path / "messages.x").mkdir()

    with pytest.raises(ValueError, match="Invalid target language"):
        _po_output_path(source, "x/../../escaped")

    assert not (tmp_path.parent / "escaped.po").exists()


def test_atomic_serializer_failure_preserves_existing_file(tmp_path):
    destination = tmp_path / "translated.txt"
    destination.write_text("previous", encoding="utf-8")

    def broken_serializer():
        raise RuntimeError("serialization failed")

    with pytest.raises(RuntimeError, match="serialization failed"):
        _write_atomic(destination, broken_serializer)

    assert destination.read_text(encoding="utf-8") == "previous"
    assert not list(tmp_path.glob(".translated.txt.*.tmp"))


@pytest.mark.parametrize("suffix", [".po", ".json", ".txt", ".md", ".srt"])
def test_write_failure_preserves_previous_outputs(
    tmp_path,
    monkeypatch,
    suffix,
):
    source, output = _translation_fixture(tmp_path, suffix)
    previous_output = b"previous output"
    output.write_bytes(previous_output)
    incomplete = output.with_suffix(".srt.incomplete")
    if suffix == ".srt":
        incomplete.write_bytes(b"previous checkpoint")

    def fail_fsync(_fd):
        raise OSError("simulated write failure")

    monkeypatch.setattr(file_translator.os, "fsync", fail_fsync)

    result = FileTranslator(EchoTranslationAPI(), source).translate_all(["fr"])

    assert result == {"fr": False}
    assert output.read_bytes() == previous_output
    if suffix == ".srt":
        assert incomplete.read_bytes() == b"previous checkpoint"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("suffix", [".po", ".json", ".txt", ".md", ".srt"])
def test_outputs_replace_symlinks_without_touching_targets(tmp_path, suffix):
    source, output = _translation_fixture(tmp_path, suffix)
    victim = tmp_path / f"victim-{suffix.removeprefix('.')}.txt"
    victim.write_text("SECRET", encoding="utf-8")
    output.symlink_to(victim)
    api = EchoTranslationAPI()

    checkpoint = output.with_suffix(".srt.incomplete")
    checkpoint_metadata = Path(f"{checkpoint}.json")
    checkpoint_victim = tmp_path / "checkpoint-victim.srt"
    metadata_victim = tmp_path / "checkpoint-metadata-victim.json"
    if suffix == ".srt":
        checkpoint_victim.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nATTACKER CHECKPOINT\n",
            encoding="utf-8",
        )
        checkpoint.symlink_to(checkpoint_victim)
        metadata_victim.write_text('{"do_not_touch": true}', encoding="utf-8")
        checkpoint_metadata.symlink_to(metadata_victim)

    result = FileTranslator(api, source).translate_all(["fr"])

    assert result == {"fr": True}
    assert victim.read_text(encoding="utf-8") == "SECRET"
    assert output.is_file()
    assert not output.is_symlink()
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    _assert_translated_output(output, suffix)
    if suffix == ".srt":
        assert api.batches == [["Hello", "World"]]
        assert checkpoint_victim.read_text(encoding="utf-8").endswith(
            "ATTACKER CHECKPOINT\n"
        )
        assert metadata_victim.read_text(encoding="utf-8") == (
            '{"do_not_touch": true}'
        )
        assert not checkpoint.exists()
        assert not checkpoint_metadata.exists()


def test_atomic_write_preserves_existing_regular_permissions(tmp_path):
    source, output = _translation_fixture(tmp_path, ".json")
    output.write_text("previous", encoding="utf-8")
    output.chmod(0o640)

    result = FileTranslator(EchoTranslationAPI(), source).translate_all(["fr"])

    assert result == {"fr": True}
    assert stat.S_IMODE(output.stat().st_mode) == 0o640


def test_srt_checkpoint_is_invalidated_when_source_changes(tmp_path):
    source = tmp_path / "episode.eng.srt"

    def srt_content(first_text):
        blocks = []
        for index in range(1, 17):
            text = first_text if index == 1 else f"Line {index}"
            blocks.append(
                f"{index}\n"
                f"00:00:{index:02d},000 --> 00:00:{index:02d},900\n"
                f"{text}"
            )
        return "\n\n".join(blocks) + "\n"

    source.write_text(srt_content("Old source"), encoding="utf-8")
    cancel_event = threading.Event()

    def cancel_after_first_batch(_lang, status, _current, _total):
        if status.startswith("translating:"):
            cancel_event.set()

    first_api = EchoTranslationAPI()
    first_result = FileTranslator(first_api, source).translate_all(
        ["fr"],
        progress_callback=cancel_after_first_batch,
        cancel_event=cancel_event,
    )
    output = _file_output_path(source, "fr", ".srt")
    checkpoint = output.with_suffix(".srt.incomplete")
    checkpoint_metadata = Path(f"{checkpoint}.json")

    assert first_result == {}
    assert checkpoint.exists()
    assert checkpoint_metadata.exists()

    source.write_text(srt_content("New source"), encoding="utf-8")
    second_api = EchoTranslationAPI()

    second_result = FileTranslator(second_api, source).translate_all(["fr"])

    assert second_result == {"fr": True}
    assert len(second_api.batches) == 2
    assert second_api.batches[0][0] == "New source"
    assert "fr:New source" in output.read_text(encoding="utf-8")
    assert not checkpoint.exists()
    assert not checkpoint_metadata.exists()


class ItalicAwareAPI(EchoTranslationAPI):
    """Echo API that mangles the markup of one specific line."""

    def __init__(self, corrupt_source: str):
        super().__init__()
        self.corrupt_source = corrupt_source
        self.individual_calls: list[str] = []

    def _render(self, text: str, target_lang: str) -> str:
        if text == self.corrupt_source:
            # Model dropped the protected token entirely.
            return f"{target_lang}:mangled"
        return f"{target_lang}:{text}"

    def translate_batch(self, texts, source_lang, target_lang):
        self.batches.append(list(texts))
        return [self._render(text, target_lang) for text in texts]

    def translate(self, text, source_lang, target_lang):
        self.individual_calls.append(text)
        return self._render(text, target_lang)


def test_srt_keeps_rejected_line_in_source_and_still_writes_file(tmp_path):
    source = tmp_path / "episode.eng.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n<i>Whispered line</i>\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nPlain line\n",
        encoding="utf-8",
    )
    api = ItalicAwareAPI("<x1/>Whispered line<x2/>")
    statuses: list[str] = []

    result = FileTranslator(api, source).translate_all(
        ["fr"],
        progress_callback=lambda _lang, status, *_: statuses.append(status),
    )

    output = _file_output_path(source, "fr", ".srt")
    content = output.read_text(encoding="utf-8")

    # One bad line must not abort the file: both blocks are present.
    assert result == {"fr": False}
    assert "<i>Whispered line</i>" in content
    assert "fr:Plain line" in content
    assert statuses[-1] == "partial: 1 lines kept in source"
    assert not output.with_suffix(".srt.incomplete").exists()


def test_srt_italics_survive_a_model_that_rewrites_the_token(tmp_path):
    source = tmp_path / "episode.eng.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n<i>Whispered line</i>\n",
        encoding="utf-8",
    )

    class SloppyTokenAPI(EchoTranslationAPI):
        def translate_batch(self, texts, source_lang, target_lang):
            self.batches.append(list(texts))
            # Case changed and HTML-escaped: both must be repaired.
            return [
                text.replace("<x1/>", "&lt;X1/&gt;")
                .replace("<x2/>", "< x2 />")
                .replace("Whispered line", f"{target_lang}:Whispered line")
                for text in texts
            ]

    result = FileTranslator(SloppyTokenAPI(), source).translate_all(["fr"])

    output = _file_output_path(source, "fr", ".srt")
    assert result == {"fr": True}
    assert "<i>fr:Whispered line</i>" in output.read_text(encoding="utf-8")
