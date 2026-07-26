"""Motor de tradução que coordena APIs e arquivos .po."""

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import threading
import polib
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Callable, Optional
from datetime import datetime

from core.languages import SUPPORTED_LANGUAGES, get_plural_rule, resolve_po_path
from api.base import TranslationAPI

log = logging.getLogger(__name__)

_PRINTF_PATTERN = re.compile(
    r"%(?:"
    r"%"
    r"|(?:\([^)]+\)|\d+\$)?"
    r"[-+#0 'I]*"
    r"(?:\*(?:\d+\$)?|\d+)?"
    r"(?:\.(?:\*(?:\d+\$)?|\d*))?"
    r"(?:hh|ll|[hlLjztq])?"
    r"[diouxXaAeEfFgGcCsSpnrb]"
    r")"
)
_BRACE_PATTERN = re.compile(
    r"(?<!\{)\{(?!\{)(?:[^{}]|\{[^{}]*\})*\}(?!\})"
)
_FORMAT_PATTERNS = [
    _PRINTF_PATTERN,
    _BRACE_PATTERN,
]


def _save_po_atomic(po: polib.POFile, path: Path) -> None:
    """Save a catalog atomically without following a destination symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (
        stat.S_IMODE(path.stat().st_mode)
        if path.exists() and not path.is_symlink()
        else 0o644
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(fd)
    try:
        po.save(temporary_name)
        os.chmod(temporary_name, mode)
        with open(temporary_name, "rb") as temporary:
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _protect_placeholders(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Substitui placeholders por tokens XML antes de traduzir.

    APIs de tradução preservam tags XML, então usamos <x1/>, <x2/> etc.
    como tokens seguros para proteger os placeholders de formato.
    Cada ocorrência recebe um token único, mesmo que o placeholder se repita.
    """
    tokens: List[Tuple[str, str]] = []

    # Coleta todos os matches com posição (para substituir de trás para frente)
    all_matches: List[Tuple[int, int, str]] = []
    for pattern in _FORMAT_PATTERNS:
        for match in pattern.finditer(text):
            all_matches.append((match.start(), match.end(), match.group()))

    # Ordena por posição (de trás para frente) para não invalidar offsets
    all_matches.sort(key=lambda m: m[0], reverse=True)

    # Atribui tokens em ordem reversa
    counter = len(all_matches)
    for start, end, placeholder in all_matches:
        token = f"<x{counter}/>"
        tokens.append((token, placeholder))
        text = text[:start] + token + text[end:]
        counter -= 1

    # Reverte a lista para que tokens fiquem em ordem natural (x1, x2, ...)
    tokens.reverse()
    return text, tokens


def _match_newlines(msgid: str, msgstr: str) -> str:
    """Ensure msgstr has the same leading/trailing newlines as msgid."""
    if not msgstr:
        return msgstr
    # Match leading newlines
    lead_orig = len(msgid) - len(msgid.lstrip("\n"))
    lead_trans = len(msgstr) - len(msgstr.lstrip("\n"))
    if lead_orig > lead_trans:
        msgstr = "\n" * (lead_orig - lead_trans) + msgstr
    elif lead_trans > lead_orig:
        msgstr = msgstr[lead_trans - lead_orig:]
    # Match trailing newlines
    trail_orig = len(msgid) - len(msgid.rstrip("\n"))
    trail_trans = len(msgstr) - len(msgstr.rstrip("\n"))
    if trail_orig > trail_trans:
        msgstr = msgstr.rstrip("\n") + "\n" * trail_orig
    elif trail_trans > trail_orig:
        msgstr = msgstr.rstrip("\n") + "\n" * trail_orig
    return msgstr


def _restore_placeholders(text: str, tokens: List[Tuple[str, str]]) -> str:
    """Restaura placeholders originais a partir dos tokens XML.

    Handles common API corruptions: extra spaces, case changes,
    HTML-encoded tags, and stray residuals.
    """
    for token, original in tokens:
        # Extract the number from <xN/>
        num = re.search(r"x(\d+)", token)
        if num:
            n = num.group(1)
            # Try multiple corruption patterns LLMs commonly produce
            variants = [
                token,                          # <x1/>
                f"<x{n} />",                    # <x1 />
                f"< x{n}/>",                    # < x1/>
                f"< x{n} />",                   # < x1 />
                f"<X{n}/>",                      # <X1/>
                f"<X{n} />",                    # <X1 />
                f"&lt;x{n}/&gt;",               # HTML-encoded
                f"<x{n}>",                      # Missing / (not self-closing)
                f"[x{n}]",                      # Bracket variant
                f"x{n}",                        # Stripped tags entirely
            ]
            for variant in variants:
                if variant in text:
                    text = text.replace(variant, original)
                    break
        else:
            text = text.replace(token, original)
    # Remove any residual XML placeholder tokens the API might have mangled
    text = re.sub(r"</?[xX]\d+\s*/?>", "", text)
    return text


def _validate_placeholders(original: str, translated: str) -> bool:
    """Validate placeholder identity and unsafe positional ordering."""
    for pattern in _FORMAT_PATTERNS:
        original_matches = pattern.findall(original)
        translated_matches = pattern.findall(translated)

        def explicitly_addressed(placeholder: str) -> bool:
            if pattern is _PRINTF_PATTERN:
                return (
                    placeholder == "%%"
                    or placeholder.startswith("%(")
                    or bool(re.match(r"%\d+\$", placeholder))
                )
            field = placeholder[1:-1].split("!", 1)[0].split(":", 1)[0]
            return bool(field)

        can_reorder = all(
            explicitly_addressed(placeholder)
            for placeholder in original_matches + translated_matches
        )
        if can_reorder:
            matches_equal = Counter(original_matches) == Counter(
                translated_matches
            )
        else:
            matches_equal = original_matches == translated_matches
        if not matches_equal:
            return False
    return True


def _fix_placeholders(original: str, translated: str) -> str:
    """Tenta reparar placeholders corrompidos na tradução.

    Handles three cases:
    1. Placeholder RENAMED by the LLM (e.g. {langs} → {kieli}) —
       match by position and replace translated names with originals.
    2. Placeholder with corrupted syntax (e.g. {word without closing }).
    3. Placeholder missing entirely — append it.
    """
    for pattern in _FORMAT_PATTERNS:
        original_matches = pattern.findall(original)
        translated_match_objects = list(pattern.finditer(translated))
        translated_matches = [
            match.group() for match in translated_match_objects
        ]

        if not original_matches:
            # No placeholders in original — remove any the LLM invented
            for match in reversed(translated_match_objects):
                translated = (
                    translated[: match.start()] + translated[match.end() :]
                )
            continue

        # Quick path: counts match but names differ → positional rename
        if (
            len(original_matches) == len(translated_matches)
            and original_matches != translated_matches
        ):
            replacements = zip(
                translated_match_objects,
                original_matches,
            )
            for match, replacement in reversed(list(replacements)):
                translated = (
                    translated[: match.start()]
                    + replacement
                    + translated[match.end() :]
                )
            continue

        # If LLM added extra placeholders not in original, remove them
        wanted = Counter(original_matches)
        extra_match_objects = []
        for match in translated_match_objects:
            placeholder = match.group()
            if wanted[placeholder] > 0:
                wanted[placeholder] -= 1
            else:
                extra_match_objects.append(match)
        for match in reversed(extra_match_objects):
            translated = translated[: match.start()] + translated[match.end() :]

        available = Counter(pattern.findall(translated))
        missing = []
        for placeholder in original_matches:
            if available[placeholder] > 0:
                available[placeholder] -= 1
            else:
                missing.append(placeholder)
        if missing:
            translated = translated.rstrip() + " " + " ".join(missing)
    return translated


def _is_translation_plausible(msgid: str, msgstr: str) -> bool:
    """Basic heuristic to detect completely wrong translations (batch shifting).

    Checks:
    - Length ratio (translation shouldn't be >4x or <0.15x the original)
    - Placeholder mismatch (strict)
    - If original has no placeholders but translation adds them, it's wrong
    """
    if not msgid or not msgstr:
        return True

    # Length ratio check (translations can vary a lot, but 4x is extreme)
    len_ratio = len(msgstr) / max(len(msgid), 1)
    if len_ratio > 5.0 or len_ratio < 0.1:
        log.debug("Implausible length ratio %.1f for '%s'", len_ratio, msgid[:40])
        return False

    # If original has no format placeholders but translation does → wrong
    orig_has_ph = any(p.search(msgid) for p in _FORMAT_PATTERNS)
    trans_has_ph = any(p.search(msgstr) for p in _FORMAT_PATTERNS)
    if not orig_has_ph and trans_has_ph:
        log.debug("Translation has placeholders but original doesn't: '%s'", msgid[:40])
        return False

    return True


def _normalize_plural_entry(entry: polib.POEntry, forms: int) -> None:
    """Keep valid plural values while enforcing the target form indexes."""
    existing = dict(entry.msgstr_plural)
    entry.msgstr = ""
    entry.msgstr_plural = {
        index: existing.get(index, existing.get(str(index), ""))
        for index in range(forms)
    }


def _plural_source(entry: polib.POEntry, index: int, forms: int) -> str:
    """Return the source form whose placeholders apply to a target index."""
    if forms == 1 or index > 0:
        return entry.msgid_plural
    return entry.msgid


def _finalize_form_translation(
    api: TranslationAPI,
    original: str,
    protected: str,
    tokens: List[Tuple[str, str]],
    candidate,
    lang: str,
) -> Tuple[str, bool]:
    """Restore and validate one translated gettext form."""

    def restore(value):
        if not isinstance(value, str) or not value:
            return None
        return _restore_placeholders(value, tokens) if tokens else value

    translated = restore(candidate)
    if translated is None or not _is_translation_plausible(original, translated):
        try:
            translated = restore(
                api.translate(
                    protected,
                    "en",
                    lang,
                )
            )
        except Exception:
            translated = None

    if translated is None or not _is_translation_plausible(original, translated):
        return original, False

    if not _validate_placeholders(original, translated):
        translated = _fix_placeholders(original, translated)
        if not _validate_placeholders(original, translated):
            return original, False

    translated = _match_newlines(original, translated)
    if not translated or not _validate_placeholders(original, translated):
        return original, False
    return translated, True


def _validate_entry_translation(
    entry: polib.POEntry,
    forms: int,
    fill_singular: bool,
) -> Tuple[bool, int]:
    """Validate every output form and replace irreparable values with source."""
    valid = True
    fixed = 0

    if entry.msgid_plural:
        entry.msgstr = ""
        for index in range(forms):
            original = _plural_source(entry, index, forms)
            translated = entry.msgstr_plural.get(index, "")
            if not translated:
                entry.msgstr_plural[index] = original
                valid = False
                fixed += 1
                continue
            if _validate_placeholders(original, translated):
                continue
            repaired = _fix_placeholders(original, translated)
            if _validate_placeholders(original, repaired):
                entry.msgstr_plural[index] = repaired
            else:
                entry.msgstr_plural[index] = original
                valid = False
            fixed += 1
        return valid, fixed

    if not entry.msgstr:
        if fill_singular:
            entry.msgstr = entry.msgid
            return False, 1
        return True, 0
    if _validate_placeholders(entry.msgid, entry.msgstr):
        return True, 0

    repaired = _fix_placeholders(entry.msgid, entry.msgstr)
    if _validate_placeholders(entry.msgid, repaired):
        entry.msgstr = repaired
        return True, 1
    entry.msgstr = entry.msgid
    return False, 1


def _save_context_cache(
    cache_path: Path,
    checked: set[str],
    changed: set[str],
    fixed_langs: set[str],
    fingerprint: str,
) -> None:
    """Persist fix_context progress for resume after cancellation."""
    payload = json.dumps(
        {
            "version": 2,
            "fingerprint": fingerprint,
            "checked": sorted(checked),
            "changed": sorted(changed),
            "fixed_langs": sorted(fixed_langs),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{cache_path.name}.",
        suffix=".tmp",
        dir=cache_path.parent,
    )
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(payload)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, cache_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _entry_identity(entry: polib.POEntry) -> str:
    """Return a stable context-aware identity for one gettext entry."""
    return json.dumps(
        [entry.msgctxt or "", entry.msgid, entry.msgid_plural or ""],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _entry_matches(entry: polib.POEntry, selectors: set[str]) -> bool:
    """Match current context-aware selectors and legacy msgid selectors."""
    return _entry_identity(entry) in selectors or entry.msgid in selectors


def _context_cache_fingerprint(
    pot_file: Path,
    locale_dir: Path,
    reference_lang: str,
    languages: list[str],
    api: TranslationAPI,
) -> str:
    """Fingerprint source catalogs and the translation implementation."""
    digest = hashlib.sha256()
    api_identity = {
        "class": f"{type(api).__module__}.{type(api).__qualname__}",
        "model": getattr(api, "model", getattr(api, "model_name", "")),
        "reference_lang": reference_lang,
        "languages": sorted(set(languages)),
    }
    digest.update(
        json.dumps(api_identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    )
    paths = [Path(pot_file)]
    paths.extend(
        resolve_po_path(locale_dir, lang)
        for lang in sorted(set(languages) | {reference_lang})
    )
    for path in paths:
        digest.update(b"\0")
        digest.update(path.name.encode("utf-8", errors="surrogateescape"))
        if path.is_symlink():
            digest.update(b"symlink")
        elif path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"missing")
    return digest.hexdigest()


class TranslationEngine:
    """Motor de tradução para projetos gettext."""

    def __init__(self, api_client: TranslationAPI, textdomain: str):
        self.api = api_client
        self.textdomain = textdomain
        self.last_language_complete = True

    def translate_project(
        self,
        pot_file: Path,
        project_path: Path,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        languages: Optional[List[str]] = None,
        force_retranslate: bool = False,
        detail_callback: Optional[Callable[[str, List[Tuple[str, str, str]]], None]] = None,
    ) -> Dict[str, bool]:
        """
        Traduz projeto para os idiomas selecionados (ou todos).

        Args:
            pot_file: Arquivo .pot template
            project_path: Diretório do projeto
            progress_callback: Callback(lang_code, status, current, total)
            cancel_event: Threading event to signal cancellation
            languages: List of language codes to translate; None = all
            force_retranslate: If True, retranslate all entries including already translated ones

        Returns:
            Dict com resultado de cada idioma {lang: success}
        """
        lang_list = languages if languages else list(SUPPORTED_LANGUAGES.keys())
        results = {}
        total_langs = len(lang_list)
        current = 0

        for lang_code in lang_list:
            if cancel_event and cancel_event.is_set():
                break
            current += 1

            # Report batch-level progress within each language
            def _batch_progress(done: int, total: int, _lc: str = lang_code) -> None:
                if progress_callback and total > 0:
                    progress_callback(
                        _lc,
                        f"translating: {done}/{total} strings",
                        current,
                        total_langs,
                    )

            try:
                strings_translated = self.translate_language(
                    pot_file,
                    lang_code,
                    project_path,
                    force_retranslate=force_retranslate,
                    cancel_event=cancel_event,
                    detail_callback=(lambda pairs, _lc=lang_code: detail_callback(_lc, pairs)) if detail_callback else None,
                    batch_progress=_batch_progress,
                )
                results[lang_code] = self.last_language_complete

                if progress_callback:
                    status = (
                        f"success: {strings_translated} strings"
                        if self.last_language_complete
                        else "error: incomplete translation"
                    )
                    progress_callback(
                        lang_code,
                        status,
                        current,
                        total_langs,
                    )
            except Exception as e:
                results[lang_code] = False
                if progress_callback:
                    progress_callback(lang_code, f"error: {e}", current, total_langs)

        return results

    def translate_language(
        self,
        pot_file: Path,
        lang: str,
        project_path: Path,
        force_retranslate: bool = False,
        fix_msgids: Optional[set] = None,
        batch_progress: Optional[Callable[[int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        detail_callback: Optional[Callable[[List[Tuple[str, str, str]]], None]] = None,
    ) -> int:
        """
        Traduz um idioma específico.

        Args:
            pot_file: Arquivo .pot template
            lang: Código do idioma (ex: 'pt-BR')
            project_path: Diretório do projeto
            force_retranslate: If True, retranslate ALL entries (fix context)
            batch_progress: Optional callback(done_strings, total_strings)

        Returns:
            Número de strings traduzidas
        """
        # Carrega template .pot
        self.last_language_complete = True
        pot_file = Path(pot_file)
        if pot_file.is_symlink():
            raise ValueError("Template catalog cannot be a symlink")
        if not pot_file.is_file():
            raise FileNotFoundError(f"Template catalog not found: {pot_file}")
        pot = polib.pofile(str(pot_file))
        plural_rule = get_plural_rule(lang)

        # Provide app context to LLM-based APIs (name + sample strings)
        context_strings = [e.msgid for e in pot if e.msgid][:20]
        self.api.set_context(self.textdomain, context_strings)

        # Caminho do arquivo .po — derive from pot_file location.
        # Use POSIX/gettext form (pt_BR.po, not pt-BR.po) for new files;
        # fall back to legacy hyphenated path if it already exists.
        locale_dir = pot_file.parent
        po_path = resolve_po_path(locale_dir, lang)

        # Cria ou carrega arquivo .po
        if po_path.is_file() and not po_path.is_symlink():
            po = polib.pofile(str(po_path))
            # Merge com pot (adiciona novas entries)
            po.merge(pot)
        else:
            # Cria novo arquivo a partir do template
            po = polib.POFile()
            po.metadata = self._create_metadata(lang)
            # Copia entries do pot
            for entry in pot:
                po.append(entry)

        po.metadata["Language"] = lang
        po.metadata["Plural-Forms"] = plural_rule.header
        po.encoding = "utf-8"
        for entry in po:
            if entry.msgid_plural and not entry.obsolete:
                _normalize_plural_entry(entry, plural_rule.forms)

        if force_retranslate:
            # Retranslate ALL entries (including already translated ones)
            entries_to_translate = [e for e in po if e.msgid and not e.obsolete]
        else:
            # Only untranslated + fuzzy
            entries_to_translate = list(po.untranslated_entries()) + list(
                po.fuzzy_entries()
            )

        # If fix_msgids is set, also include translated entries whose msgid is in that set
        if fix_msgids:
            selected_entries = {id(entry) for entry in entries_to_translate}
            for entry in po:
                if (
                    _entry_matches(entry, fix_msgids)
                    and not entry.obsolete
                    and id(entry) not in selected_entries
                ):
                    entries_to_translate.append(entry)
                    selected_entries.add(id(entry))

        unique_entries = []
        selected_entries = set()
        for entry in entries_to_translate:
            if entry.msgid and not entry.obsolete and id(entry) not in selected_entries:
                unique_entries.append(entry)
                selected_entries.add(id(entry))
        entries_to_translate = unique_entries

        # The source catalog is English. Copy it without spending API calls.
        if lang == "en":
            copied_count = 0
            for entry in entries_to_translate:
                if not entry.msgid or entry.obsolete:
                    continue
                if entry.msgid_plural:
                    entry.msgstr = ""
                    for plural_index in range(plural_rule.forms):
                        entry.msgstr_plural[plural_index] = _plural_source(
                            entry,
                            plural_index,
                            plural_rule.forms,
                        )
                else:
                    entry.msgstr = entry.msgid
                if "fuzzy" in entry.flags:
                    entry.flags.remove("fuzzy")
                copied_count += 1
            _save_po_atomic(po, po_path)
            self.last_language_complete = True
            return copied_count

        translation_tasks = []
        entry_task_totals = {}
        entry_task_results = {}

        def add_task(entry, original, plural_indexes=None):
            entry_id = id(entry)
            translation_tasks.append((entry, original, plural_indexes))
            entry_task_totals[entry_id] = entry_task_totals.get(entry_id, 0) + 1
            entry_task_results.setdefault(entry_id, [])

        for entry in entries_to_translate:
            retranslate_all = (
                force_retranslate
                or "fuzzy" in entry.flags
                or (
                    fix_msgids is not None
                    and _entry_matches(entry, fix_msgids)
                )
            )
            if not entry.msgid_plural:
                add_task(entry, entry.msgid)
                continue

            if plural_rule.forms == 1:
                indexes = tuple(
                    index
                    for index in range(plural_rule.forms)
                    if retranslate_all or not entry.msgstr_plural[index]
                )
                if indexes:
                    add_task(entry, entry.msgid_plural, indexes)
                continue

            if retranslate_all or not entry.msgstr_plural[0]:
                add_task(entry, entry.msgid, (0,))
            plural_indexes = tuple(
                index
                for index in range(1, plural_rule.forms)
                if retranslate_all or not entry.msgstr_plural[index]
            )
            if plural_indexes:
                add_task(entry, entry.msgid_plural, plural_indexes)

        # Use batch translation when available (reduces API calls dramatically).
        batch_size = 15
        for batch_start in range(0, len(translation_tasks), batch_size):
            if cancel_event and cancel_event.is_set():
                break

            if batch_start > 0 and self.api.batch_delay > 0:
                import time as _time

                _time.sleep(self.api.batch_delay)

            batch_tasks = translation_tasks[batch_start : batch_start + batch_size]
            protected_texts = []
            token_maps = []
            for _entry, original, _plural_indexes in batch_tasks:
                protected, tokens = _protect_placeholders(original)
                protected_texts.append(protected)
                token_maps.append(tokens)

            try:
                translations = self.api.translate_batch(
                    texts=protected_texts,
                    source_lang="en",
                    target_lang=lang,
                )
                if len(translations) != len(protected_texts):
                    raise ValueError(
                        "Batch translation cardinality mismatch: "
                        f"expected {len(protected_texts)}, "
                        f"got {len(translations)}"
                    )
            except Exception:
                # Each form gets one individual retry during finalization.
                translations = [None] * len(protected_texts)

            detail_pairs = []
            for task, protected, tokens, translation in zip(
                batch_tasks,
                protected_texts,
                token_maps,
                translations,
            ):
                entry, original, plural_indexes = task
                translated, succeeded = _finalize_form_translation(
                    self.api,
                    original,
                    protected,
                    tokens,
                    translation,
                    lang,
                )
                if plural_indexes is None:
                    entry.msgstr = translated
                else:
                    entry.msgstr = ""
                    for plural_index in plural_indexes:
                        entry.msgstr_plural[plural_index] = translated
                entry_task_results[id(entry)].append(succeeded)
                detail_pairs.append((original, translated, ""))

            if detail_callback and detail_pairs:
                detail_callback(detail_pairs)

            if batch_progress:
                batch_progress(
                    min(batch_start + batch_size, len(translation_tasks)),
                    len(translation_tasks),
                )

        selected_entry_ids = {id(entry) for entry in entries_to_translate}
        validation_results = {}
        fixed_in_validation = 0
        for entry in po:
            if not entry.msgid or entry.obsolete:
                continue
            valid, fixed = _validate_entry_translation(
                entry,
                plural_rule.forms,
                fill_singular=id(entry) in selected_entry_ids,
            )
            validation_results[id(entry)] = valid
            fixed_in_validation += fixed
            if not valid and "fuzzy" not in entry.flags:
                entry.flags.append("fuzzy")

        translated_count = 0
        language_complete = True
        for entry in entries_to_translate:
            entry_id = id(entry)
            task_results = entry_task_results.get(entry_id, [])
            succeeded = (
                entry_task_totals.get(entry_id, 0) > 0
                and len(task_results) == entry_task_totals[entry_id]
                and all(task_results)
                and validation_results.get(entry_id, False)
            )
            if succeeded:
                if "fuzzy" in entry.flags:
                    entry.flags.remove("fuzzy")
                translated_count += 1
            else:
                language_complete = False
                if "fuzzy" not in entry.flags:
                    entry.flags.append("fuzzy")

        if fixed_in_validation:
            log.info(
                "Post-save validation fixed %d forms in %s",
                fixed_in_validation,
                lang,
            )

        _save_po_atomic(po, po_path)
        self.last_language_complete = language_complete
        return translated_count

    def fix_context(
        self,
        pot_file: Path,
        project_path: Path,
        reference_lang: str,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        languages: Optional[List[str]] = None,
        detail_callback: Optional[Callable[[str, List[Tuple[str, str, str]]], None]] = None,
    ) -> Dict[str, bool]:
        """Re-translate only entries whose context-aware translation differs.

        1. Retranslate all entries for *reference_lang* using context.
        2. Compare with existing translations — collect msgids that changed.
        3. For all other languages, retranslate only those msgids.

        Supports resuming: saves progress to a cache file so that cancelled
        runs can be continued without re-checking already verified entries.
        """
        pot_file = Path(pot_file)
        if pot_file.is_symlink():
            raise ValueError("Template catalog cannot be a symlink")
        if not pot_file.is_file():
            raise FileNotFoundError(f"Template catalog not found: {pot_file}")
        pot = polib.pofile(str(pot_file))
        context_strings = [e.msgid for e in pot if e.msgid][:20]
        self.api.set_context(self.textdomain, context_strings)

        locale_dir = pot_file.parent
        ref_po_path = resolve_po_path(locale_dir, reference_lang)
        cache_path = locale_dir / ".langforge_context_cache.json"
        lang_list = languages if languages else list(SUPPORTED_LANGUAGES.keys())
        fingerprint_languages = list(set(lang_list) | {reference_lang})
        cache_fingerprint = _context_cache_fingerprint(
            pot_file,
            locale_dir,
            reference_lang,
            fingerprint_languages,
            self.api,
        )
        log.info(
            "fix_context: ref_po_path=%s exists=%s", ref_po_path, ref_po_path.exists()
        )
        if ref_po_path.is_symlink():
            raise ValueError("Reference catalog cannot be a symlink")
        if not ref_po_path.is_file():
            raise FileNotFoundError(
                f"Reference catalog not found: {ref_po_path}"
            )

        # Load resume cache only when its complete source state still matches.
        already_checked: set[str] = set()
        changed_msgids: set[str] = set()
        fixed_langs: set[str] = set()
        if cache_path.is_file() and not cache_path.is_symlink():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    isinstance(cache, dict)
                    and cache.get("version") == 2
                    and cache.get("fingerprint") == cache_fingerprint
                ):
                    already_checked = {
                        value
                        for value in cache.get("checked", [])
                        if isinstance(value, str)
                    }
                    changed_msgids = {
                        value
                        for value in cache.get("changed", [])
                        if isinstance(value, str)
                    }
                    fixed_langs = {
                        value
                        for value in cache.get("fixed_langs", [])
                        if isinstance(value, str) and value in lang_list
                    }
                    log.info(
                        "fix_context: resuming — %d checked, %d changed, "
                        "%d langs fixed",
                        len(already_checked),
                        len(changed_msgids),
                        len(fixed_langs),
                    )
            except (OSError, TypeError, ValueError):
                log.warning("Ignoring invalid fix-context cache")

        # Phase 1: find entries that change when translated with context (batch)
        if ref_po_path.exists():
            ref_po = polib.pofile(str(ref_po_path))
            reference_rule = get_plural_rule(reference_lang)
            ref_po.metadata["Language"] = reference_lang
            ref_po.metadata["Plural-Forms"] = reference_rule.header
            for entry in ref_po:
                if entry.msgid_plural and not entry.obsolete:
                    _normalize_plural_entry(entry, reference_rule.forms)
                    valid, _fixed = _validate_entry_translation(
                        entry,
                        reference_rule.forms,
                        fill_singular=False,
                    )
                    if not valid and "fuzzy" not in entry.flags:
                        entry.flags.append("fuzzy")
            entries_by_key = {
                _entry_identity(entry): entry
                for entry in ref_po.translated_entries()
                if entry.msgid
            }
            all_entry_keys = set(entries_by_key)
            already_checked.intersection_update(all_entry_keys)
            changed_msgids.intersection_update(all_entry_keys)
            all_entries = list(entries_by_key.values())
            remaining = [
                entry
                for entry in all_entries
                if _entry_identity(entry) not in already_checked
            ]
            total_entries = len(all_entry_keys)
            checked = len(already_checked)
            log.info(
                "fix_context: %d total, %d already checked, %d remaining",
                total_entries,
                checked,
                len(remaining),
            )

            if progress_callback:
                progress_callback(
                    reference_lang,
                    f"checking context: {checked}/{total_entries}",
                    checked,
                    total_entries,
                )

            translation_tasks = []
            entry_task_totals = {}
            entry_task_results = {}
            entry_task_values = {}
            for entry in remaining:
                entry_id = id(entry)
                if not entry.msgid_plural:
                    tasks = [(entry.msgid, None)]
                elif reference_rule.forms == 1:
                    tasks = [(entry.msgid_plural, (0,))]
                else:
                    tasks = [
                        (entry.msgid, (0,)),
                        (
                            entry.msgid_plural,
                            tuple(range(1, reference_rule.forms)),
                        ),
                    ]
                entry_task_totals[entry_id] = len(tasks)
                entry_task_results[entry_id] = []
                entry_task_values[entry_id] = []
                for original, plural_indexes in tasks:
                    translation_tasks.append((entry, original, plural_indexes))

            processed_entries = set()

            def process_completed_entries():
                nonlocal checked
                for entry in remaining:
                    entry_id = id(entry)
                    if entry_id in processed_entries:
                        continue
                    task_results = entry_task_results[entry_id]
                    if len(task_results) != entry_task_totals[entry_id]:
                        continue
                    processed_entries.add(entry_id)
                    if not all(task_results):
                        continue

                    changed = False
                    for plural_indexes, translated in entry_task_values[entry_id]:
                        if plural_indexes is None:
                            if translated.strip() != entry.msgstr.strip():
                                entry.msgstr = translated
                                changed = True
                            continue
                        for plural_index in plural_indexes:
                            previous = entry.msgstr_plural[plural_index]
                            if translated.strip() != previous.strip():
                                entry.msgstr_plural[plural_index] = translated
                                changed = True

                    checked += 1
                    entry_key = _entry_identity(entry)
                    already_checked.add(entry_key)
                    if changed:
                        changed_msgids.add(entry_key)
                        log.info(
                            "fix_context: CHANGED '%s'",
                            entry.msgid[:40],
                        )
                        if "fuzzy" in entry.flags:
                            entry.flags.remove("fuzzy")

            batch_size = 15
            for batch_start in range(0, len(translation_tasks), batch_size):
                if cancel_event and cancel_event.is_set():
                    break

                if batch_start > 0 and self.api.batch_delay > 0:
                    import time as _time

                    _time.sleep(self.api.batch_delay)

                batch_tasks = translation_tasks[
                    batch_start : batch_start + batch_size
                ]
                protected_texts = []
                token_maps = []
                for _entry, original, _plural_indexes in batch_tasks:
                    protected, tokens = _protect_placeholders(original)
                    protected_texts.append(protected)
                    token_maps.append(tokens)

                try:
                    new_translations = self.api.translate_batch(
                        texts=protected_texts,
                        source_lang="en",
                        target_lang=reference_lang,
                    )
                    if len(new_translations) != len(protected_texts):
                        raise ValueError(
                            "Batch translation cardinality mismatch: "
                            f"expected {len(protected_texts)}, "
                            f"got {len(new_translations)}"
                        )
                except Exception:
                    new_translations = [None] * len(protected_texts)

                for task, protected, tokens, new_translation in zip(
                    batch_tasks,
                    protected_texts,
                    token_maps,
                    new_translations,
                ):
                    entry, original, plural_indexes = task
                    translated, succeeded = _finalize_form_translation(
                        self.api,
                        original,
                        protected,
                        tokens,
                        new_translation,
                        reference_lang,
                    )
                    entry_task_results[id(entry)].append(succeeded)
                    entry_task_values[id(entry)].append(
                        (plural_indexes, translated)
                    )

                process_completed_entries()

                if progress_callback:
                    progress_callback(
                        reference_lang,
                        f"checking context: {checked}/{total_entries} "
                        f"({len(changed_msgids)} to fix)",
                        checked,
                        total_entries,
                    )

            _save_po_atomic(ref_po, ref_po_path)

            # Save cache for resume
            cache_fingerprint = _context_cache_fingerprint(
                pot_file,
                locale_dir,
                reference_lang,
                fingerprint_languages,
                self.api,
            )
            _save_context_cache(
                cache_path,
                already_checked,
                changed_msgids,
                fixed_langs,
                cache_fingerprint,
            )

            was_cancelled = bool(cancel_event and cancel_event.is_set())
            reference_complete = all_entry_keys <= already_checked
            if was_cancelled:
                return {}
            if not reference_complete:
                if progress_callback:
                    progress_callback(
                        reference_lang,
                        "error: incomplete context check",
                        checked,
                        total_entries,
                    )
                return {reference_lang: False}

            if progress_callback:
                progress_callback(
                    reference_lang,
                    f"checking context: done — {len(changed_msgids)} entries to fix",
                    total_entries,
                    total_entries,
                )

        if not changed_msgids:
            # All done — remove cache
            cache_path.unlink(missing_ok=True)
            return {}

        # Phase 2: fix those entries in all other languages
        # Remove reference language (already fixed) and already-fixed langs
        other_langs = [
            lc for lc in lang_list if lc != reference_lang and lc not in fixed_langs
        ]
        results: Dict[str, bool] = {reference_lang: True}
        total_langs = len(lang_list)  # all languages including reference

        # Report reference language as done (it was the baseline)
        if progress_callback:
            progress_callback(
                reference_lang,
                "success: reference language",
                1,
                total_langs,
            )

        # Report already-fixed langs so the UI starts at the right offset
        # Reference lang counts as 1, then each fixed lang adds 1
        done_count = 1  # reference lang
        for done_lang in sorted(fixed_langs):
            results[done_lang] = True
            done_count += 1
            if progress_callback:
                progress_callback(
                    done_lang,
                    "success: already fixed",
                    done_count,
                    total_langs,
                )

        for i, lang in enumerate(other_langs):
            if cancel_event and cancel_event.is_set():
                break
            lang_idx = done_count + i + 1
            log.info(
                "fix_context phase2: translating %s (%d/%d)",
                lang,
                lang_idx,
                total_langs,
            )

            # Sub-language progress callback for UI feedback
            def _batch_cb(
                done_strings: int, total_strings: int, _lang=lang, _idx=lang_idx
            ) -> None:
                if progress_callback:
                    progress_callback(
                        _lang,
                        f"translating: {done_strings}/{total_strings} strings",
                        _idx,
                        total_langs,
                    )

            if progress_callback:
                progress_callback(
                    lang, "translating: starting...", lang_idx, total_langs
                )

            try:
                self.translate_language(
                    pot_file,
                    lang,
                    project_path,
                    fix_msgids=changed_msgids,
                    batch_progress=_batch_cb,
                    cancel_event=cancel_event,
                    detail_callback=(lambda pairs, _lc=lang: detail_callback(_lc, pairs)) if detail_callback else None,
                )
                if self.last_language_complete:
                    results[lang] = True
                    fixed_langs.add(lang)
                    status = f"success: fixed {len(changed_msgids)} entries"
                else:
                    results[lang] = False
                    status = "error: incomplete translation"
                if progress_callback:
                    progress_callback(
                        lang,
                        status,
                        done_count + i + 1,
                        total_langs,
                    )
            except Exception as e:
                results[lang] = False
                if progress_callback:
                    progress_callback(
                        lang, f"error: {e}", done_count + i + 1, total_langs
                    )

            # Save cache periodically for resume
            cache_fingerprint = _context_cache_fingerprint(
                pot_file,
                locale_dir,
                reference_lang,
                fingerprint_languages,
                self.api,
            )
            _save_context_cache(
                cache_path,
                already_checked,
                changed_msgids,
                fixed_langs,
                cache_fingerprint,
            )

        # If all languages completed, remove cache
        all_langs = set(lang_list)
        if fixed_langs | {reference_lang} >= all_langs:
            cache_path.unlink(missing_ok=True)

        return results

    def _create_metadata(self, lang: str) -> Dict[str, str]:
        """Cria metadata para arquivo .po."""
        plural_rule = get_plural_rule(lang)
        return {
            "Project-Id-Version": self.textdomain,
            "Report-Msgid-Bugs-To": "",
            "POT-Creation-Date": datetime.now().strftime("%Y-%m-%d %H:%M%z"),
            "PO-Revision-Date": datetime.now().strftime("%Y-%m-%d %H:%M%z"),
            "Last-Translator": "Translation Automator <auto@translator.ai>",
            "Language-Team": f"{SUPPORTED_LANGUAGES[lang]} <{lang}@li.org>",
            "Language": lang,
            "MIME-Version": "1.0",
            "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": "8bit",
            "Plural-Forms": plural_rule.header,
        }
