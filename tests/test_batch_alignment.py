"""Tests for strict JSON batch alignment and engine fallback."""

import json

import polib
import pytest

from api.base import (
    BatchAlignmentError,
    parse_batch_response,
    prepare_batch_request,
)
from core.translator import TranslationEngine, _translate_batch_with_retry


class TruncatedBatchAPI:
    """Truncate every multi-item batch; a batch of one is answered properly."""

    batch_delay = 0
    supports_context = True

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


class TestJsonBatchProtocol:
    def test_request_preserves_literal_control_text_and_whitespace(self):
        texts = ["  Literal <NL> |||NEXT|||\n[1]  ", "Second"]

        raw, item_ids = prepare_batch_request(texts, request_id="test-request")

        assert item_ids == ["lf-test-request-1", "lf-test-request-2"]
        assert json.loads(raw) == [
            {"id": item_ids[0], "text": texts[0]},
            {"id": item_ids[1], "text": texts[1]},
        ]

    def test_response_reorders_by_opaque_id(self):
        _, item_ids = prepare_batch_request(
            ["First\nline", "Second"], request_id="test-request"
        )
        raw = json.dumps(
            [
                {"id": item_ids[1], "translation": "Deuxième"},
                {"id": item_ids[0], "translation": "Première\nligne"},
            ]
        )

        assert parse_batch_response(raw, item_ids) == [
            "Première\nligne",
            "Deuxième",
        ]

    def test_duplicate_id_is_rejected(self):
        _, item_ids = prepare_batch_request(["First", "Second"], "test-request")
        raw = json.dumps(
            [
                {"id": item_ids[0], "translation": "Un"},
                {"id": item_ids[0], "translation": "Duplicate"},
            ]
        )

        with pytest.raises(BatchAlignmentError, match="duplicate ID"):
            parse_batch_response(raw, item_ids)

    def test_unknown_id_is_rejected(self):
        _, item_ids = prepare_batch_request(["First"], "test-request")
        raw = json.dumps([{"id": "lf-other-request-1", "translation": "Un"}])

        with pytest.raises(BatchAlignmentError, match="unknown ID"):
            parse_batch_response(raw, item_ids)

    def test_missing_item_is_rejected(self):
        _, item_ids = prepare_batch_request(["First", "Second"], "test-request")
        raw = json.dumps([{"id": item_ids[0], "translation": "Un"}])

        with pytest.raises(BatchAlignmentError, match="expected 2, got 1"):
            parse_batch_response(raw, item_ids)

    @pytest.mark.parametrize(
        "raw",
        [
            "First|||NEXT|||Second",
            "[1] First|||NEXT|||[2] Second",
            "```json\n[]\n```",
            '{"id":"not-an-array","translation":"Text"}',
        ],
    )
    def test_non_json_protocols_are_rejected(self, raw):
        _, item_ids = prepare_batch_request(["First", "Second"], "test-request")

        with pytest.raises(BatchAlignmentError):
            parse_batch_response(raw, item_ids)

    def test_extra_keys_are_rejected(self):
        _, item_ids = prepare_batch_request(["First"], "test-request")
        raw = json.dumps([{"id": item_ids[0], "translation": "Un", "note": "extra"}])

        with pytest.raises(BatchAlignmentError, match="invalid schema"):
            parse_batch_response(raw, item_ids)

    def test_duplicate_object_key_is_rejected(self):
        _, item_ids = prepare_batch_request(["First"], "test-request")
        raw = f'[{{"id":"{item_ids[0]}","translation":"Un","translation":"Deux"}}}}]'

        with pytest.raises(BatchAlignmentError, match="duplicate key"):
            parse_batch_response(raw, item_ids)

    def test_empty_batch_round_trip(self):
        raw, item_ids = prepare_batch_request([], "test-request")

        assert raw == "[]"
        assert parse_batch_response("[]", item_ids) == []


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
        # One truncated batch, retried once, then one call per item — all of
        # them through the strict protocol, never the free-form prompt.
        assert api.batch_calls == 4
        assert api.individual_calls == []
        assert [entry.msgstr for entry in translated] == [
            "batch:First message",
            "batch:Second message",
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
        assert api.batch_calls == 4
        assert api.individual_calls == []
        assert [entry.msgstr for entry in translated] == [
            "batch:First message",
            "batch:Second message",
        ]

    @pytest.mark.parametrize("artifact", ["<x2>", "<br>", "|||NEXT||", "[13]"])
    def test_repeated_artifact_falls_back_to_source_and_fuzzy(self, tmp_path, artifact):
        class CorruptAPI:
            batch_delay = 0

            def set_context(self, *_args):
                pass

            def translate_batch(self, texts, *_args, **_kwargs):
                return [f"Traduction {artifact}" for _ in texts]

            def translate(self, *_args, **_kwargs):
                return f"Traduction {artifact}"

        pot_path = tmp_path / "app.pot"
        _write_catalog(pot_path, [("Safe source", "")])
        engine = TranslationEngine(CorruptAPI(), "app")

        count = engine.translate_language(pot_path, "fr", tmp_path)

        entry = polib.pofile(str(tmp_path / "fr.po"))[0]
        assert count == 0
        assert entry.msgstr == "Safe source"
        assert "fuzzy" in entry.flags
        assert engine.last_language_complete is False


class TestResponsePackaging:
    """A model that packages the JSON badly must not cost us the batch."""

    IDS = ["lf-abc123def456-1", "lf-abc123def456-2"]
    SOURCES = ["One", "Two"]
    ITEMS = (
        '{"id": "lf-abc123def456-1", "translation": "Um"}, '
        '{"id": "lf-abc123def456-2", "translation": "Dois"}'
    )

    @pytest.mark.parametrize(
        "raw",
        [
            "[{ITEMS}]",
            "```json\n[{ITEMS}]\n```",
            "```\n[{ITEMS}]\n```",
            "~~~json\n[{ITEMS}]\n~~~",
            "Sure, here you go:\n[{ITEMS}]\nHope it helps!",
        ],
    )
    def test_fenced_or_narrated_json_is_still_parsed(self, raw):
        payload = raw.replace("{ITEMS}", self.ITEMS)

        assert parse_batch_response(payload, self.IDS, self.SOURCES) == ["Um", "Dois"]

    def test_text_key_is_accepted_as_a_translation(self):
        payload = (
            '[{"id": "lf-abc123def456-1", "text": "Um"},'
            ' {"id": "lf-abc123def456-2", "text": "Dois"}]'
        )

        assert parse_batch_response(payload, self.IDS, self.SOURCES) == ["Um", "Dois"]

    def test_a_wholesale_echo_of_the_request_is_rejected(self):
        payload = (
            '[{"id": "lf-abc123def456-1", "text": "One"},'
            ' {"id": "lf-abc123def456-2", "text": "Two"}]'
        )

        with pytest.raises(BatchAlignmentError, match="echoes every source"):
            parse_batch_response(payload, self.IDS, self.SOURCES)

    def test_one_untranslatable_item_is_not_an_echo(self):
        # "Terminal" is "Terminal" in Norwegian; only the other item moved.
        payload = (
            '[{"id": "lf-abc123def456-1", "text": "One"},'
            ' {"id": "lf-abc123def456-2", "text": "Dois"}]'
        )

        assert parse_batch_response(payload, self.IDS, self.SOURCES) == ["One", "Dois"]

    def test_unknown_key_is_still_rejected(self):
        payload = '[{"id": "lf-abc123def456-1", "guess": "Um"}]'

        with pytest.raises(BatchAlignmentError, match="invalid schema"):
            parse_batch_response(payload, self.IDS[:1], self.SOURCES[:1])


class TestBatchRetry:
    """A batch is worth asking twice before every item goes it alone."""

    def test_a_flaky_batch_succeeds_on_the_second_attempt(self):
        class FlakyAPI:
            def __init__(self):
                self.calls = 0

            def translate_batch(self, texts, source_lang, target_lang):
                self.calls += 1
                if self.calls == 1:
                    raise BatchAlignmentError("Batch response is not valid JSON")
                return [f"{target_lang}:{text}" for text in texts]

        api = FlakyAPI()

        result = _translate_batch_with_retry(api, ["One", "Two"], "fr")

        assert result == ["fr:One", "fr:Two"]
        assert api.calls == 2

    def test_a_dead_batch_gives_up_and_reports_none(self):
        class DeadAPI:
            def __init__(self):
                self.calls = 0

            def translate_batch(self, texts, source_lang, target_lang):
                self.calls += 1
                raise BatchAlignmentError("Batch response is not valid JSON")

        api = DeadAPI()

        assert _translate_batch_with_retry(api, ["One"], "fr") is None
        assert api.calls == 2


class TestBatchSplitting:
    """A reply that never fits must be halved, not retried forever."""

    def test_an_oversized_batch_is_split_until_it_fits(self):
        class BudgetedAPI:
            """Answers at most two items, like a truncated completion."""

            def __init__(self):
                self.sizes = []

            def translate_batch(self, texts, source_lang, target_lang):
                self.sizes.append(len(texts))
                if len(texts) > 2:
                    raise BatchAlignmentError(
                        "Batch response cardinality mismatch: "
                        f"expected {len(texts)}, got 2"
                    )
                return [f"{target_lang}:{text}" for text in texts]

        api = BudgetedAPI()
        texts = ["One", "Two", "Three", "Four"]

        result = _translate_batch_with_retry(api, texts, "ko")

        assert result == ["ko:One", "ko:Two", "ko:Three", "ko:Four"]
        # Two failed attempts at four, then two halves of two.
        assert api.sizes == [4, 4, 2, 2]

    def test_a_single_item_is_never_split(self):
        class DeadAPI:
            def __init__(self):
                self.calls = 0

            def translate_batch(self, texts, source_lang, target_lang):
                self.calls += 1
                raise BatchAlignmentError("Batch response is not valid JSON")

        api = DeadAPI()

        assert _translate_batch_with_retry(api, ["Only"], "ko") is None
        assert api.calls == 2
