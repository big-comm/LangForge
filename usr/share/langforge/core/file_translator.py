"""Translate individual files (non-gettext project mode).

Supports: .po/.pot, .json, .txt, .md, .srt
"""

import copy
import errno
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

import polib

from api.base import TranslationAPI
from core.languages import FILE_LANG_CODES, get_file_lang_code, get_plural_rule
from core.translator import (
    _finalize_form_translation,
    _protect_placeholders,
    _translate_batch_with_retry,
)

log = logging.getLogger(__name__)

# File extensions grouped by handler
_PO_EXTS = {".po", ".pot"}
_JSON_EXTS = {".json"}
_TEXT_EXTS = {".txt", ".md", ".markdown", ".rst"}
_SRT_EXTS = {".srt"}

SUPPORTED_EXTENSIONS = _PO_EXTS | _JSON_EXTS | _TEXT_EXTS | _SRT_EXTS
_BLANK_LINE_SEPARATOR = re.compile(r"((?:\r?\n[ \t]*){2,})")
_MARKDOWN_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def is_supported_file(path: Path) -> bool:
    """Check whether a file can be translated by FileTranslator."""
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _file_output_path(source: Path, lang: str, ext: str) -> Path:
    """Build output path using 3-letter ISO 639-2 language codes.

    'Sequestro S02E07.eng.srt' + 'pt-BR' → 'Sequestro S02E07.por-BR.srt'
    'myfile.srt' + 'pt-BR' → 'myfile.por-BR.srt'
    """
    stem = source.stem
    target_code = _safe_output_component(
        get_file_lang_code(lang),
        "target language code",
    )

    # Strip known source language suffix (e.g. '.eng' from stem)
    all_codes = {c.lower() for c in FILE_LANG_CODES.values()}
    parts = stem.rsplit(".", 1)
    source_code = parts[1].lower() if len(parts) == 2 else ""
    if source_code in all_codes and source_code != target_code.lower():
        stem = parts[0]

    output = source.parent / f"{stem}.{target_code}{ext}"
    return _ensure_distinct_output(source, output)


def _po_output_path(source: Path, lang: str) -> Path:
    """Build a distinct .po output path for a translated catalog."""
    safe_lang = _safe_output_component(lang, "target language")
    output = source.parent / f"{source.stem}.{safe_lang}.po"
    return _ensure_distinct_output(source, output)


def _safe_output_component(value: str, label: str) -> str:
    """Reject values that could introduce a directory into an output path."""
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\0" in value
    ):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _ensure_distinct_output(source: Path, output: Path) -> Path:
    """Reject output paths that alias the source file."""
    if output.parent != source.parent:
        raise ValueError(f"Output path escapes source directory: {output}")
    if source.absolute() == output.absolute():
        raise ValueError(f"Output path would overwrite source file: {source}")
    try:
        if source.samefile(output):
            raise ValueError(f"Output path would overwrite source file: {source}")
    except FileNotFoundError:
        pass
    return output


def _write_atomic(
    path: Path,
    serialize: Callable[[], str | bytes],
    *,
    encoding: str = "utf-8",
) -> None:
    """Serialize and atomically replace a file without following symlinks."""
    content = serialize()
    if isinstance(content, str):
        payload = content.encode(encoding)
    elif isinstance(content, bytes):
        payload = content
    else:
        raise TypeError("Serializer must return text or bytes")

    try:
        current = path.lstat()
    except FileNotFoundError:
        mode = 0o644
    else:
        mode = (
            stat.S_IMODE(current.st_mode) & 0o777
            if stat.S_ISREG(current.st_mode)
            else 0o644
        )

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("Atomic write made no progress")
            remaining = remaining[written:]
        os.fchmod(fd, mode)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary_path, path)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _read_regular_text(path: Path, *, encoding: str = "utf-8") -> str | None:
    """Read an existing regular file without following a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            return None
        raise

    try:
        opened = os.fstat(fd)
        try:
            current = path.lstat()
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            return None

        chunks: list[bytes] = []
        while chunk := os.read(fd, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks).decode(encoding)
    finally:
        os.close(fd)


def _translate_texts_exact(
    api: TranslationAPI,
    texts: list[str],
    target_lang: str,
) -> tuple[list[str], bool]:
    """Translate one batch with exact alignment and placeholder validation."""
    translations, item_results = _translate_texts_with_status(
        api,
        texts,
        target_lang,
    )
    return translations, all(item_results)


def _translate_texts_with_status(
    api: TranslationAPI,
    texts: list[str],
    target_lang: str,
) -> tuple[list[str], list[bool]]:
    """Translate one batch and retain each item's validation result."""
    if target_lang == "en":
        return list(texts), [True] * len(texts)

    protected_texts: list[str] = []
    token_maps = []
    for text in texts:
        protected, tokens = _protect_placeholders(text)
        protected_texts.append(protected)
        token_maps.append(tokens)

    candidates = _translate_batch_with_retry(api, protected_texts, target_lang)
    if candidates is None:
        candidates = [None] * len(protected_texts)

    translations: list[str] = []
    item_results: list[bool] = []
    for original, protected, tokens, candidate in zip(
        texts,
        protected_texts,
        token_maps,
        candidates,
    ):
        translated, item_succeeded = _finalize_form_translation(
            api,
            original,
            protected,
            tokens,
            candidate,
            target_lang,
        )
        translations.append(translated)
        item_results.append(item_succeeded)
    return translations, item_results


def _json_string_leaves(value, path=()):
    """Yield paths and non-empty string leaves from nested JSON data."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _json_string_leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _json_string_leaves(item, (*path, index))
    elif isinstance(value, str) and value.strip():
        yield path, value


def _set_json_path(value, path, replacement) -> None:
    """Set one already-validated JSON path."""
    current = value
    for component in path[:-1]:
        current = current[component]
    current[path[-1]] = replacement


def _markdown_fence_ranges(content: str) -> list[tuple[int, int]]:
    """Return byte-independent character ranges for fenced Markdown code."""
    ranges: list[tuple[int, int]] = []
    opening_start: int | None = None
    opening_char = ""
    opening_length = 0
    offset = 0

    for line in content.splitlines(keepends=True):
        match = _MARKDOWN_FENCE.match(line)
        if opening_start is None:
            if match:
                marker = match.group(1)
                opening_start = offset
                opening_char = marker[0]
                opening_length = len(marker)
        elif match:
            marker = match.group(1)
            remainder = line[match.end(1) :].rstrip("\r\n")
            is_closing = not remainder.strip(" \t")
            if (
                marker[0] == opening_char
                and len(marker) >= opening_length
                and is_closing
            ):
                ranges.append((opening_start, offset + len(line)))
                opening_start = None
        offset += len(line)

    if opening_start is not None:
        ranges.append((opening_start, len(content)))
    return ranges


def _text_segments(content: str, markdown: bool) -> tuple[list[str], list[tuple[int, str]]]:
    """Split text without normalizing whitespace and exclude Markdown fences."""
    segments: list[str] = []
    tasks: list[tuple[int, str]] = []

    def append_prose(prose: str) -> None:
        for part in _BLANK_LINE_SEPARATOR.split(prose):
            if not part:
                continue
            if _BLANK_LINE_SEPARATOR.fullmatch(part):
                segments.append(part)
                continue
            leading_length = len(part) - len(part.lstrip())
            trailing_length = len(part) - len(part.rstrip())
            end = len(part) - trailing_length if trailing_length else len(part)
            core = part[leading_length:end]
            if not core:
                segments.append(part)
                continue
            index = len(segments)
            segments.append(part[:leading_length] + core + part[end:])
            tasks.append((index, core))

    if not markdown:
        append_prose(content)
        return segments, tasks

    cursor = 0
    for start, end in _markdown_fence_ranges(content):
        append_prose(content[cursor:start])
        segments.append(content[start:end])
        cursor = end
    append_prose(content[cursor:])
    return segments, tasks


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

        entries = [e for e in pot if e.msgid and not e.obsolete]
        if not entries:
            return {}

        context_strings = [e.msgid for e in entries[:20]]
        self.api.set_context(self.source_file.stem, context_strings)

        # Translate singular and plural source forms independently.
        translation_tasks: list[tuple[int, str, str]] = []
        for entry_index, entry in enumerate(entries):
            translation_tasks.append((entry_index, "singular", entry.msgid))
            if entry.msgid_plural:
                translation_tasks.append((entry_index, "plural", entry.msgid_plural))

        originals = [text for _, _, text in translation_tasks]
        results: dict[str, bool] = {}
        total = len(langs)
        batch_size = 15

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                plural_rule = get_plural_rule(lang)
                po = copy.deepcopy(pot)
                po.metadata = {
                    **pot.metadata,
                    "Language": lang,
                    "Content-Type": "text/plain; charset=UTF-8",
                    "Plural-Forms": plural_rule.header,
                }
                po.encoding = "utf-8"

                translated_texts: list[str] = []
                translation_results: list[bool] = []
                language_succeeded = True

                for batch_start in range(0, len(originals), batch_size):
                    if cancel_event and cancel_event.is_set():
                        break
                    if batch_start > 0 and self.api.batch_delay > 0:
                        import time as _time

                        _time.sleep(self.api.batch_delay)

                    batch = originals[batch_start : batch_start + batch_size]
                    batch_results, batch_item_results = (
                        _translate_texts_with_status(
                            self.api,
                            batch,
                            lang,
                        )
                    )
                    batch_succeeded = all(batch_item_results)
                    language_succeeded = language_succeeded and batch_succeeded
                    translated_texts.extend(batch_results)
                    translation_results.extend(batch_item_results)

                    # Emit detail pairs
                    if detail_cb:
                        pairs = [
                            (batch[j], batch_results[j], "")
                            for j in range(len(batch_results))
                        ]
                        detail_cb(lang, pairs)

                    if progress_cb:
                        done = min(batch_start + batch_size, len(originals))
                        progress_cb(
                            lang,
                            f"translating: {done}/{len(originals)} strings",
                            i + 1,
                            total,
                        )

                if cancel_event and cancel_event.is_set():
                    break

                translated_forms: dict[int, dict[str, str]] = {}
                entry_results: dict[int, list[bool]] = {}
                for task, translated, succeeded in zip(
                    translation_tasks,
                    translated_texts,
                    translation_results,
                ):
                    entry_index, form, _ = task
                    translated_forms.setdefault(entry_index, {})[form] = translated
                    entry_results.setdefault(entry_index, []).append(succeeded)

                translated_entries = [
                    entry for entry in po if entry.msgid and not entry.obsolete
                ]
                for entry_index, forms in translated_forms.items():
                    entry = translated_entries[entry_index]
                    if entry.msgid_plural:
                        singular = forms.get("singular")
                        plural = forms.get("plural")
                        entry.msgstr_plural = {}
                        for plural_index in range(plural_rule.forms):
                            source = (
                                plural
                                if plural_rule.forms == 1 or plural_index > 0
                                else singular
                            )
                            entry.msgstr_plural[plural_index] = source or (
                                entry.msgid_plural
                                if plural_rule.forms == 1 or plural_index > 0
                                else entry.msgid
                            )
                        entry.msgstr = ""
                    elif "singular" in forms:
                        entry.msgstr = forms["singular"]
                    if all(entry_results.get(entry_index, [])):
                        if "fuzzy" in entry.flags:
                            entry.flags.remove("fuzzy")
                    elif "fuzzy" not in entry.flags:
                        entry.flags.append("fuzzy")

                po.metadata_is_fuzzy = not language_succeeded
                out = _po_output_path(self.source_file, lang)
                _write_atomic(out, po.__unicode__, encoding=po.encoding)

                results[lang] = language_succeeded
                if progress_cb:
                    status = "success" if language_succeeded else "error: incomplete"
                    progress_cb(lang, status, i + 1, total)
            except Exception as e:
                log.warning(
                    "Failed translating %s to %s: %s", self.source_file.name, lang, e
                )
                results[lang] = False
                if progress_cb:
                    progress_cb(lang, f"error: {e}", i + 1, total)

        return results

    # ── .json ───────────────────────────────────────────────────

    def _translate_json(self, langs, progress_cb, cancel_event, detail_cb):
        with open(self.source_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        if not isinstance(data, (dict, list)):
            raise ValueError("JSON root must be an object or array")

        leaves = list(_json_string_leaves(data))
        sample_values = [value for _, value in leaves[:20]]
        self.api.set_context(self.source_file.stem, sample_values)

        paths_to_translate = [path for path, _ in leaves]
        texts_to_translate = [value for _, value in leaves]

        results: dict[str, bool] = {}
        total = len(langs)
        batch_size = 15

        for i, lang in enumerate(langs):
            if cancel_event and cancel_event.is_set():
                break
            try:
                translated_values: list[str] = []
                language_succeeded = True

                for batch_start in range(0, len(texts_to_translate), batch_size):
                    if cancel_event and cancel_event.is_set():
                        break
                    if batch_start > 0 and self.api.batch_delay > 0:
                        import time as _time

                        _time.sleep(self.api.batch_delay)

                    batch = texts_to_translate[batch_start : batch_start + batch_size]
                    batch_paths = paths_to_translate[
                        batch_start : batch_start + batch_size
                    ]
                    batch_results, batch_succeeded = _translate_texts_exact(
                        self.api,
                        batch,
                        lang,
                    )
                    language_succeeded = language_succeeded and batch_succeeded
                    translated_values.extend(batch_results)

                    # Emit detail pairs for this batch
                    if detail_cb:
                        pairs = [
                            (orig, trans, repr(path))
                            for orig, trans, path in zip(
                                batch, batch_results, batch_paths
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

                translated = copy.deepcopy(data)
                for path, value in zip(paths_to_translate, translated_values):
                    _set_json_path(translated, path, value)

                out = _file_output_path(self.source_file, lang, ".json")
                _write_atomic(
                    out,
                    lambda: json.dumps(
                        translated,
                        ensure_ascii=False,
                        indent=2,
                    ),
                )

                results[lang] = language_succeeded
                if progress_cb:
                    status = "success" if language_succeeded else "error: incomplete"
                    progress_cb(lang, status, i + 1, total)
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
        with open(self.source_file, "r", encoding="utf-8", newline="") as fh:
            content = fh.read()

        segments, translatable = _text_segments(
            content,
            markdown=self.ext in {".md", ".markdown"},
        )

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
                language_succeeded = True

                for batch_start in range(0, len(texts), batch_size):
                    if cancel_event and cancel_event.is_set():
                        break
                    if batch_start > 0 and self.api.batch_delay > 0:
                        import time as _time

                        _time.sleep(self.api.batch_delay)

                    batch = texts[batch_start : batch_start + batch_size]
                    batch_results, batch_succeeded = _translate_texts_exact(
                        self.api,
                        batch,
                        lang,
                    )
                    language_succeeded = language_succeeded and batch_succeeded
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

                output_segments = list(segments)
                trans_iter = iter(translated_texts)
                for idx, _ in translatable:
                    original = output_segments[idx]
                    leading_length = len(original) - len(original.lstrip())
                    trailing_length = len(original) - len(original.rstrip())
                    end = (
                        len(original) - trailing_length
                        if trailing_length
                        else len(original)
                    )
                    output_segments[idx] = (
                        original[:leading_length]
                        + next(trans_iter, original[leading_length:end])
                        + original[end:]
                    )

                out = _file_output_path(self.source_file, lang, self.ext)
                _write_atomic(out, lambda: "".join(output_segments))

                results[lang] = language_succeeded
                if progress_cb:
                    status = "success" if language_succeeded else "error: incomplete"
                    progress_cb(lang, status, i + 1, total)
            except Exception as e:
                log.warning(
                    "Failed translating %s to %s: %s", self.source_file.name, lang, e
                )
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
            checkpoint_metadata_path = Path(f"{incomplete_path}.json")
            source_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

            try:
                language_succeeded = True
                untranslated = 0
                # Resume: load already translated subtitles
                translated_texts: list[str] = []
                incomplete_content = _read_regular_text(incomplete_path)
                checkpoint_metadata_content = _read_regular_text(
                    checkpoint_metadata_path
                )
                if (
                    incomplete_content is not None
                    and checkpoint_metadata_content is not None
                ):
                    partial = self._parse_srt(incomplete_content)
                    try:
                        checkpoint_metadata = json.loads(
                            checkpoint_metadata_content
                        )
                    except (TypeError, ValueError):
                        checkpoint_metadata = {}
                    checkpoint_digest = hashlib.sha256(
                        incomplete_content.encode("utf-8")
                    ).hexdigest()
                    resume_matches = (
                        isinstance(checkpoint_metadata, dict)
                        and checkpoint_metadata.get("version") == 1
                        and checkpoint_metadata.get("source_sha256") == source_digest
                        and checkpoint_metadata.get("checkpoint_sha256")
                        == checkpoint_digest
                        and checkpoint_metadata.get("completed") == len(partial)
                        and 0 < len(partial) <= len(subtitles)
                        and all(
                            item["index"] == subtitles[index]["index"]
                            and item["timecode"] == subtitles[index]["timecode"]
                            for index, item in enumerate(partial)
                        )
                    )
                    if resume_matches:
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
                    batch_results, item_results = _translate_texts_with_status(
                        self.api,
                        batch,
                        lang,
                    )

                    # Emit detail pairs for this batch
                    if detail_cb:
                        pairs = [
                            (orig, trans, f"#{idx}")
                            for orig, trans, idx in zip(
                                batch, batch_results, batch_indices
                            )
                        ]
                        detail_cb(lang, pairs)

                    # A rejected line keeps its source text instead of aborting
                    # the whole subtitle: the file stays complete and playable.
                    untranslated += sum(1 for ok in item_results if not ok)
                    language_succeeded = language_succeeded and all(item_results)
                    translated_texts.extend(batch_results)

                    # Save progress to .incomplete file after each batch
                    partial_content = self._build_srt_content(
                        subtitles[: len(translated_texts)], translated_texts
                    )
                    _write_atomic(incomplete_path, lambda: partial_content)
                    checkpoint_metadata = json.dumps(
                        {
                            "version": 1,
                            "source_sha256": source_digest,
                            "checkpoint_sha256": hashlib.sha256(
                                partial_content.encode("utf-8")
                            ).hexdigest(),
                            "completed": len(translated_texts),
                        },
                        sort_keys=True,
                    )
                    _write_atomic(
                        checkpoint_metadata_path,
                        lambda: checkpoint_metadata,
                    )

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

                # Every block has text: write final file and remove .incomplete
                final_content = self._build_srt_content(subtitles, translated_texts)
                _write_atomic(final_path, lambda: final_content)
                incomplete_path.unlink(missing_ok=True)
                checkpoint_metadata_path.unlink(missing_ok=True)

                if not language_succeeded:
                    log.warning(
                        "SRT for %s kept %d/%d lines in the source language",
                        lang,
                        untranslated,
                        len(subtitles),
                    )
                results[lang] = language_succeeded
                if progress_cb:
                    status = (
                        "success"
                        if language_succeeded
                        else f"partial: {untranslated} lines kept in source"
                    )
                    progress_cb(lang, status, i + 1, total)
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
