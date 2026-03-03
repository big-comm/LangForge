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
    ) -> dict[str, bool]:
        """Translate the file to every language in *target_langs*.

        Returns a dict ``{lang_code: success_bool}``.
        """
        if self.ext in _PO_EXTS:
            return self._translate_po(target_langs, progress_callback, cancel_event)
        if self.ext in _JSON_EXTS:
            return self._translate_json(target_langs, progress_callback, cancel_event)
        if self.ext in _SRT_EXTS:
            return self._translate_srt(target_langs, progress_callback, cancel_event)
        if self.ext in _TEXT_EXTS:
            return self._translate_text(target_langs, progress_callback, cancel_event)
        raise ValueError(f"Unsupported file type: {self.ext}")

    # ── .po / .pot ──────────────────────────────────────────────

    def _translate_po(self, langs, progress_cb, cancel_event):
        pot = polib.pofile(str(self.source_file))

        # Set context on the API (app identifier = filename stem)
        context_strings = [e.msgid for e in pot if e.msgid][:20]
        self.api.set_context(self.source_file.stem, context_strings)

        results: dict[str, bool] = {}
        total = len(langs)

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

                for entry in pot:
                    if not entry.msgid:
                        continue
                    protected, tokens = _protect_placeholders(entry.msgid)
                    translated = self.api.translate(protected, "en", lang)
                    if tokens:
                        translated = _restore_placeholders(translated, tokens)
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

    def _translate_json(self, langs, progress_cb, cancel_event):
        with open(self.source_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        if not isinstance(data, dict):
            raise ValueError("JSON must be a flat key-value object")

        # Set context
        sample_values = [v for v in data.values() if isinstance(v, str)][:20]
        self.api.set_context(self.source_file.stem, sample_values)

        results: dict[str, bool] = {}
        total = len(langs)

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                translated = {}
                for key, value in data.items():
                    if isinstance(value, str) and value.strip():
                        translated[key] = self.api.translate(value, "en", lang)
                    else:
                        translated[key] = value

                out = self.source_file.parent / f"{self.source_file.stem}.{lang}.json"
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(translated, fh, ensure_ascii=False, indent=2)

                results[lang] = True
                if progress_cb:
                    progress_cb(lang, "success", i + 1, total)
            except Exception as e:
                log.warning("Failed translating %s to %s: %s", self.source_file.name, lang, e)
                results[lang] = False
                if progress_cb:
                    progress_cb(lang, f"error: {e}", i + 1, total)

        return results

    # ── .txt / .md ──────────────────────────────────────────────

    def _translate_text(self, langs, progress_cb, cancel_event):
        with open(self.source_file, "r", encoding="utf-8") as fh:
            content = fh.read()

        # Split into translatable paragraphs
        paragraphs = re.split(r"\n{2,}", content)

        # Set context
        samples = [p.strip() for p in paragraphs if p.strip()][:15]
        self.api.set_context(self.source_file.stem, samples)

        results: dict[str, bool] = {}
        total = len(langs)

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                translated_paras = []
                for para in paragraphs:
                    stripped = para.strip()
                    if stripped:
                        translated_paras.append(
                            self.api.translate(stripped, "en", lang)
                        )
                    else:
                        translated_paras.append("")

                out = self.source_file.parent / f"{self.source_file.stem}.{lang}{self.ext}"
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("\n\n".join(translated_paras))

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
        """Parse SRT content into a list of subtitle blocks.

        Each block: {'index': str, 'timecode': str, 'text': str}
        """
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

    def _translate_srt(self, langs, progress_cb, cancel_event):
        with open(self.source_file, "r", encoding="utf-8") as fh:
            content = fh.read()

        subtitles = self._parse_srt(content)
        if not subtitles:
            raise ValueError("No subtitle blocks found in SRT file")

        # Set context from first subtitle texts
        samples = [s["text"] for s in subtitles[:20]]
        self.api.set_context(self.source_file.stem, samples)

        results: dict[str, bool] = {}
        total = len(langs)

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                translated_blocks = []
                for sub in subtitles:
                    translated_text = self.api.translate(sub["text"], "en", lang)
                    translated_blocks.append(
                        f"{sub['index']}\n{sub['timecode']}\n{translated_text}"
                    )

                out = self.source_file.parent / f"{self.source_file.stem}.{lang}.srt"
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("\n\n".join(translated_blocks) + "\n")

                results[lang] = True
                if progress_cb:
                    progress_cb(lang, "success", i + 1, total)
            except Exception as e:
                log.warning("Failed translating %s to %s: %s", self.source_file.name, lang, e)
                results[lang] = False
                if progress_cb:
                    progress_cb(lang, f"error: {e}", i + 1, total)

        return results
