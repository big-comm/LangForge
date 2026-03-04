"""Translate individual files (non-gettext project mode).

Supports: .po/.pot, .json, .txt, .md, .srt
"""

import json
import logging
import re
import threading
from pathlib import Path
from typing import Callable, Optional

import polib

from api.base import TranslationAPI
from core.languages import FILE_LANG_CODES, get_file_lang_code
from core.translator import _protect_placeholders, _restore_placeholders

log = logging.getLogger(__name__)

# File extensions grouped by handler
_PO_EXTS = {".po", ".pot"}
_JSON_EXTS = {".json"}
_TEXT_EXTS = {".txt", ".md", ".markdown", ".rst"}
_SRT_EXTS = {".srt"}

SUPPORTED_EXTENSIONS = _PO_EXTS | _JSON_EXTS | _TEXT_EXTS | _SRT_EXTS


def is_supported_file(path: Path) -> bool:
    """Check whether a file can be translated by FileTranslator."""
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _file_output_path(source: Path, lang: str, ext: str) -> Path:
    """Build output path using 3-letter ISO 639-2 language codes.

    'Sequestro S02E07.eng.srt' + 'pt-BR' → 'Sequestro S02E07.por.srt'
    'myfile.srt' + 'pt-BR' → 'myfile.por.srt'
    """
    stem = source.stem
    target_code = get_file_lang_code(lang)

    # Strip known source language suffix (e.g. '.eng' from stem)
    all_codes = {c.lower() for c in FILE_LANG_CODES.values()}
    parts = stem.rsplit(".", 1)
    if len(parts) == 2 and parts[1].lower() in all_codes:
        stem = parts[0]

    return source.parent / f"{stem}.{target_code}{ext}"


class FileTranslator:
    """Translates an individual file to multiple target languages."""

    def __init__(self, api_client: TranslationAPI, source_file: Path):
        self.api = api_client
        self.source_file = source_file
        self.ext = source_file.suffix.lower()

    def translate_all(
        self,
        target_langs: list[str],
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        detail_callback: Optional[
            Callable[[str, list[tuple[str, str, str]]], None]
        ] = None,
    ) -> dict[str, bool]:
        """Translate the file to every language in *target_langs*.

        *detail_callback(lang, pairs)* receives a list of
        (original, translated, index_hint) tuples after each batch.

        Returns a dict ``{lang_code: success_bool}``.
        """
        if self.ext in _PO_EXTS:
            return self._translate_po(
                target_langs, progress_callback, cancel_event, detail_callback
            )
        if self.ext in _JSON_EXTS:
            return self._translate_json(
                target_langs, progress_callback, cancel_event, detail_callback
            )
        if self.ext in _SRT_EXTS:
            return self._translate_srt(
                target_langs, progress_callback, cancel_event, detail_callback
            )
        if self.ext in _TEXT_EXTS:
            return self._translate_text(
                target_langs, progress_callback, cancel_event, detail_callback
            )
        raise ValueError(f"Unsupported file type: {self.ext}")

    # ── .po / .pot ──────────────────────────────────────────────

    def _translate_po(self, langs, progress_cb, cancel_event, detail_cb):
        pot = polib.pofile(str(self.source_file))

        entries = [e for e in pot if e.msgid]
        if not entries:
            return {}

        context_strings = [e.msgid for e in entries[:20]]
        self.api.set_context(self.source_file.stem, context_strings)

        # Pre-process placeholders
        originals = [e.msgid for e in entries]
        protected_texts = []
        token_maps = []
        for msgid in originals:
            protected, tokens = _protect_placeholders(msgid)
            protected_texts.append(protected)
            token_maps.append(tokens)

        results: dict[str, bool] = {}
        total = len(langs)
        batch_size = 15

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                po = polib.POFile()
                po.metadata = {
                    **pot.metadata,
                    "Language": lang,
                    "Content-Type": "text/plain; charset=UTF-8",
                }

                translated_texts: list[str] = []

                for batch_start in range(0, len(protected_texts), batch_size):
                    if cancel_event and cancel_event.is_set():
                        break
                    if batch_start > 0 and self.api.batch_delay > 0:
                        import time as _time

                        _time.sleep(self.api.batch_delay)

                    batch = protected_texts[batch_start : batch_start + batch_size]
                    try:
                        batch_results = self.api.translate_batch(batch, "en", lang)
                    except Exception:
                        batch_results = []
                        for text in batch:
                            try:
                                batch_results.append(
                                    self.api.translate(text, "en", lang)
                                )
                            except Exception:
                                batch_results.append(text)

                    # Restore placeholders
                    for j, translated in enumerate(batch_results):
                        idx = batch_start + j
                        if token_maps[idx]:
                            batch_results[j] = _restore_placeholders(
                                translated, token_maps[idx]
                            )
                    translated_texts.extend(batch_results)

                    # Emit detail pairs
                    if detail_cb:
                        pairs = [
                            (originals[batch_start + j], batch_results[j], "")
                            for j in range(len(batch_results))
                        ]
                        detail_cb(lang, pairs)

                    if progress_cb:
                        done = min(batch_start + batch_size, len(protected_texts))
                        progress_cb(
                            lang,
                            f"translating: {done}/{len(protected_texts)} entries",
                            i + 1,
                            total,
                        )

                if cancel_event and cancel_event.is_set():
                    break

                # Build PO entries
                for entry, translated in zip(entries, translated_texts):
                    po.append(polib.POEntry(msgid=entry.msgid, msgstr=translated))

                out = self.source_file.parent / f"{self.source_file.stem}.{lang}{self.ext}"
                po.save(str(out))

                results[lang] = True
                if progress_cb:
                    progress_cb(lang, "success", i + 1, total)
            except Exception as e:
                log.warning("Failed translating %s to %s: %s", self.source_file.name, lang, e)
                results[lang] = False
                if progress_cb:
                    progress_cb(lang, f"error: {e}", i + 1, total)

        return results

    # ── .json ───────────────────────────────────────────────────

    def _translate_json(self, langs, progress_cb, cancel_event, detail_cb):
        with open(self.source_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        if not isinstance(data, dict):
            raise ValueError("JSON must be a flat key-value object")

        sample_values = [v for v in data.values() if isinstance(v, str)][:20]
        self.api.set_context(self.source_file.stem, sample_values)

        keys_to_translate = [
            k for k, v in data.items() if isinstance(v, str) and v.strip()
        ]
        texts_to_translate = [data[k] for k in keys_to_translate]

        results: dict[str, bool] = {}
        total = len(langs)
        batch_size = 15

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                translated_values: list[str] = []

                for batch_start in range(0, len(texts_to_translate), batch_size):
                    if cancel_event and cancel_event.is_set():
                        break
                    if batch_start > 0 and self.api.batch_delay > 0:
                        import time as _time

                        _time.sleep(self.api.batch_delay)

                    batch = texts_to_translate[batch_start : batch_start + batch_size]
                    batch_keys = keys_to_translate[
                        batch_start : batch_start + batch_size
                    ]
                    try:
                        batch_results = self.api.translate_batch(batch, "en", lang)
                    except Exception:
                        batch_results = []
                        for text in batch:
                            try:
                                batch_results.append(
                                    self.api.translate(text, "en", lang)
                                )
                            except Exception:
                                batch_results.append(text)
                    translated_values.extend(batch_results)

                    # Emit detail pairs for this batch
                    if detail_cb:
                        pairs = [
                            (orig, trans, key)
                            for orig, trans, key in zip(
                                batch, batch_results, batch_keys
                            )
                        ]
                        detail_cb(lang, pairs)

                    if progress_cb:
                        done = min(batch_start + batch_size, len(texts_to_translate))
                        progress_cb(
                            lang,
                            f"translating: {done}/{len(texts_to_translate)} keys",
                            i + 1,
                            total,
                        )

                if cancel_event and cancel_event.is_set():
                    break

                # Rebuild full dict
                translated = {}
                val_iter = iter(translated_values)
                for key, value in data.items():
                    if key in keys_to_translate:
                        translated[key] = next(val_iter, value)
                    else:
                        translated[key] = value

                out = _file_output_path(self.source_file, lang, ".json")
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(translated, fh, ensure_ascii=False, indent=2)

                results[lang] = True
                if progress_cb:
                    progress_cb(lang, "success", i + 1, total)
            except Exception as e:
                log.warning(
                    "Failed translating %s to %s: %s", self.source_file.name, lang, e
                )
                results[lang] = False
                if progress_cb:
                    progress_cb(lang, f"error: {e}", i + 1, total)

        return results

    # ── .txt / .md ──────────────────────────────────────────────

    def _translate_text(self, langs, progress_cb, cancel_event, detail_cb):
        with open(self.source_file, "r", encoding="utf-8") as fh:
            content = fh.read()

        paragraphs = re.split(r"\n{2,}", content)
        translatable = [
            (idx, p.strip()) for idx, p in enumerate(paragraphs) if p.strip()
        ]

        samples = [text for _, text in translatable[:15]]
        self.api.set_context(self.source_file.stem, samples)

        results: dict[str, bool] = {}
        total = len(langs)
        batch_size = 15

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                texts = [text for _, text in translatable]
                translated_texts: list[str] = []

                for batch_start in range(0, len(texts), batch_size):
                    if cancel_event and cancel_event.is_set():
                        break
                    if batch_start > 0 and self.api.batch_delay > 0:
                        import time as _time

                        _time.sleep(self.api.batch_delay)

                    batch = texts[batch_start : batch_start + batch_size]
                    try:
                        batch_results = self.api.translate_batch(batch, "en", lang)
                    except Exception:
                        batch_results = []
                        for text in batch:
                            try:
                                batch_results.append(
                                    self.api.translate(text, "en", lang)
                                )
                            except Exception:
                                batch_results.append(text)
                    translated_texts.extend(batch_results)

                    if detail_cb:
                        pairs = [
                            (orig, trans, f"¶{batch_start + j + 1}")
                            for j, (orig, trans) in enumerate(zip(batch, batch_results))
                        ]
                        detail_cb(lang, pairs)

                    if progress_cb:
                        done = min(batch_start + batch_size, len(texts))
                        progress_cb(
                            lang,
                            f"translating: {done}/{len(texts)} paragraphs",
                            i + 1,
                            total,
                        )

                if cancel_event and cancel_event.is_set():
                    break

                # Rebuild paragraphs with translations
                output_paras = list(paragraphs)
                trans_iter = iter(translated_texts)
                for idx, _ in translatable:
                    output_paras[idx] = next(trans_iter, output_paras[idx])

                out = _file_output_path(self.source_file, lang, self.ext)
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("\n\n".join(output_paras))

                results[lang] = True
                if progress_cb:
                    progress_cb(lang, "success", i + 1, total)
            except Exception as e:
                log.warning("Failed translating %s to %s: %s", self.source_file.name, lang, e)
                results[lang] = False
                if progress_cb:
                    progress_cb(lang, f"error: {e}", i + 1, total)

        return results

    # ── .srt (SubRip subtitles) ─────────────────────────────────

    @staticmethod
    def _parse_srt(text: str) -> list[dict]:
        """Parse SRT content into a list of subtitle blocks."""
        blocks = re.split(r"\n\n+", text.strip())
        subtitles = []
        for block in blocks:
            lines = block.strip().splitlines()
            if len(lines) < 3:
                continue
            index = lines[0].strip()
            timecode = lines[1].strip()
            sub_text = "\n".join(lines[2:])
            subtitles.append({"index": index, "timecode": timecode, "text": sub_text})
        return subtitles

    @staticmethod
    def _build_srt_content(subtitles: list[dict], translations: list[str]) -> str:
        """Build SRT file content from subtitles and translations."""
        blocks = []
        for sub, text in zip(subtitles, translations):
            blocks.append(f"{sub['index']}\n{sub['timecode']}\n{text}")
        return "\n\n".join(blocks) + "\n"

    def _translate_srt(self, langs, progress_cb, cancel_event, detail_cb):
        with open(self.source_file, "r", encoding="utf-8") as fh:
            content = fh.read()

        subtitles = self._parse_srt(content)
        if not subtitles:
            raise ValueError("No subtitle blocks found in SRT file")

        samples = [s["text"] for s in subtitles[:20]]
        self.api.set_context(self.source_file.stem, samples)

        results: dict[str, bool] = {}
        total = len(langs)
        batch_size = 15
        all_texts = [sub["text"] for sub in subtitles]

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break

            final_path = _file_output_path(self.source_file, lang, ".srt")
            incomplete_path = final_path.with_suffix(".srt.incomplete")

            try:
                # Resume: load already translated subtitles
                translated_texts: list[str] = []
                if incomplete_path.exists():
                    partial = self._parse_srt(
                        incomplete_path.read_text(encoding="utf-8")
                    )
                    if partial:
                        translated_texts = [p["text"] for p in partial]
                        log.info(
                            "Resuming SRT %s: %d/%d already done",
                            lang,
                            len(translated_texts),
                            len(subtitles),
                        )

                resume_from = len(translated_texts)

                for batch_start in range(resume_from, len(all_texts), batch_size):
                    if cancel_event and cancel_event.is_set():
                        break

                    if batch_start > resume_from and self.api.batch_delay > 0:
                        import time as _time

                        _time.sleep(self.api.batch_delay)

                    batch = all_texts[batch_start : batch_start + batch_size]
                    batch_indices = [
                        subtitles[batch_start + j]["index"] for j in range(len(batch))
                    ]
                    try:
                        batch_results = self.api.translate_batch(batch, "en", lang)
                    except Exception:
                        batch_results = []
                        for text in batch:
                            try:
                                batch_results.append(
                                    self.api.translate(text, "en", lang)
                                )
                            except Exception:
                                batch_results.append(text)
                    translated_texts.extend(batch_results)

                    # Emit detail pairs for this batch
                    if detail_cb:
                        pairs = [
                            (orig, trans, f"#{idx}")
                            for orig, trans, idx in zip(
                                batch, batch_results, batch_indices
                            )
                        ]
                        detail_cb(lang, pairs)

                    # Save progress to .incomplete file after each batch
                    partial_content = self._build_srt_content(
                        subtitles[: len(translated_texts)], translated_texts
                    )
                    incomplete_path.write_text(partial_content, encoding="utf-8")

                    if progress_cb:
                        done = len(translated_texts)
                        progress_cb(
                            lang,
                            f"translating: {done}/{len(all_texts)} subtitles",
                            i + 1,
                            total,
                        )

                # Check if cancelled or incomplete
                if cancel_event and cancel_event.is_set():
                    break

                if len(translated_texts) != len(subtitles):
                    log.warning(
                        "Incomplete SRT for %s: %d/%d",
                        lang,
                        len(translated_texts),
                        len(subtitles),
                    )
                    results[lang] = False
                    if progress_cb:
                        progress_cb(lang, "error: incomplete", i + 1, total)
                    continue

                # Complete: write final file and remove .incomplete
                final_content = self._build_srt_content(subtitles, translated_texts)
                final_path.write_text(final_content, encoding="utf-8")
                incomplete_path.unlink(missing_ok=True)

                results[lang] = True
                if progress_cb:
                    progress_cb(lang, "success", i + 1, total)
            except Exception as e:
                log.warning(
                    "Failed translating %s to %s: %s",
                    self.source_file.name,
                    lang,
                    e,
                )
                results[lang] = False
                if progress_cb:
                    progress_cb(lang, f"error: {e}", i + 1, total)

        return results
