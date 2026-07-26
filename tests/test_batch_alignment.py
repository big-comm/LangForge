"""Tests for strict LLM batch-response alignment."""

import polib
import pytest

from api.base import BatchAlignmentError, parse_batch_response
from core.translator import TranslationEngine


class TruncatedBatchAPI:
    """Return a truncated batch and deterministic individual translations."""

    batch_delay = 0

    def __init__(self):
        self.batch_calls = 0
        self.individual_calls: list[str] = []

    def set_context(self, *_args):
        pass

    def translate_batch(self, texts, source_lang, target_lang):
        self.batch_calls += 1
        return [f"batch:{texts[0]}"]

    def translate(self, text, source_lang, target_lang):
        self.individual_calls.append(text)
        return f"single:{text}"


def _write_catalog(path, entries):
    catalog = polib.POFile()
    for msgid, msgstr in entries:
        catalog.append(polib.POEntry(msgid=msgid, msgstr=msgstr))
    catalog.save(str(path))


class TestParseBatchResponse:
    def test_numbered_response_reorders_and_restores_newlines(self):
        raw = (
            "[2] Second <NL> line"
            "|||NEXT|||"
            "[1] First <NL>  <NL>"
        )

        assert parse_batch_response(raw, 2) == [
            "First\n\n",
            "Second\nline",
        ]

    def test_payload_bracket_prefix_is_preserved(self):
        assert parse_batch_response("[1] [99] keep this", 1) == [
            "[99] keep this"
        ]

    def test_duplicate_id_is_rejected(self):
        raw = "[1] First|||NEXT|||[1] Duplicate"

        with pytest.raises(BatchAlignmentError, match="duplicate ID 1"):
            parse_batch_response(raw, 2)

    @pytest.mark.parametrize("item_id", [0, -1, 3])
    def test_out_of_range_id_is_rejected(self, item_id):
        raw = f"[{item_id}] Invalid|||NEXT|||[1] First"

        with pytest.raises(BatchAlignmentError, match="outside 1..2"):
            parse_batch_response(raw, 2)

    def test_missing_id_is_rejected(self):
        raw = "[1] First|||NEXT|||[3] Third"

        with pytest.raises(BatchAlignmentError, match="missing IDs: 2"):
            parse_batch_response(raw, 3)

    def test_mixed_numbering_is_rejected(self):
        raw = "[1] First|||NEXT|||Second"

        with pytest.raises(BatchAlignmentError, match="mixes numbered"):
            parse_batch_response(raw, 2)

    def test_unnumbered_response_requires_exact_cardinality(self):
        raw = "First <NL> line|||NEXT|||Second"

        assert parse_batch_response(raw, 2) == [
            "First\nline",
            "Second",
        ]

        with pytest.raises(BatchAlignmentError, match="expected 3, got 2"):
            parse_batch_response(raw, 3)

    def test_empty_response_is_valid_only_for_empty_batch(self):
        assert parse_batch_response("", 0) == []

        with pytest.raises(BatchAlignmentError, match="expected 1, got 0"):
            parse_batch_response("", 1)


class TestTranslationEngineBatchDefense:
    def test_translate_language_retries_entire_truncated_batch(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        _write_catalog(
            pot_path,
            [("First message", ""), ("Second message", "")],
        )
        api = TruncatedBatchAPI()

        count = TranslationEngine(api, "app").translate_language(
            pot_path, "fr", tmp_path
        )

        translated = polib.pofile(str(tmp_path / "fr.po"))
        assert count == 2
        assert api.batch_calls == 1
        assert api.individual_calls == ["First message", "Second message"]
        assert [entry.msgstr for entry in translated] == [
            "single:First message",
            "single:Second message",
        ]

    def test_fix_context_retries_entire_truncated_batch(self, tmp_path):
        pot_path = tmp_path / "app.pot"
        reference_path = tmp_path / "fr.po"
        entries = [("First message", ""), ("Second message", "")]
        _write_catalog(pot_path, entries)
        _write_catalog(
            reference_path,
            [
                ("First message", "Old first"),
                ("Second message", "Old second"),
            ],
        )
        api = TruncatedBatchAPI()

        result = TranslationEngine(api, "app").fix_context(
            pot_path,
            tmp_path,
            reference_lang="fr",
            languages=["fr"],
        )

        translated = polib.pofile(str(reference_path))
        assert result == {"fr": True}
        assert api.batch_calls == 1
        assert api.individual_calls == ["First message", "Second message"]
        assert [entry.msgstr for entry in translated] == [
            "single:First message",
            "single:Second message",
        ]
