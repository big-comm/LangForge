"""Motor de tradução que coordena APIs e arquivos .po."""

import logging
import re
import threading
import polib
from pathlib import Path
from typing import Dict, List, Tuple, Callable, Optional
from datetime import datetime

from core.languages import SUPPORTED_LANGUAGES
from api.base import TranslationAPI

log = logging.getLogger(__name__)

# Padrões de formato Python que devem ser preservados durante a tradução
_FORMAT_PATTERNS = [
    re.compile(r"%\([^)]+\)[sdifcr]"),  # %(name)s, %(count)d
    re.compile(r"%[sdifcr%]"),  # %s, %d, %i, %f, %c, %r, %%
    re.compile(r"\{[^}]*\}"),  # {}, {0}, {name}, {count:.2f}
]


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
    """Valida se todos os placeholders do original existem na tradução."""
    for pattern in _FORMAT_PATTERNS:
        orig_matches = sorted(pattern.findall(original))
        trans_matches = sorted(pattern.findall(translated))
        if orig_matches != trans_matches:
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
        orig_matches = pattern.findall(original)
        trans_matches = pattern.findall(translated)

        if not orig_matches:
            # No placeholders in original — remove any the LLM invented
            for spurious in trans_matches:
                translated = translated.replace(spurious, "", 1)
            continue

        # Quick path: counts match but names differ → positional rename
        if len(orig_matches) == len(trans_matches) and orig_matches != trans_matches:
            for orig_ph, trans_ph in zip(orig_matches, trans_matches):
                if orig_ph != trans_ph:
                    translated = translated.replace(trans_ph, orig_ph, 1)
            continue

        # If LLM added extra placeholders not in original, remove them
        if len(trans_matches) > len(orig_matches):
            extra = [p for p in trans_matches if p not in orig_matches]
            for p in extra:
                translated = translated.replace(p, "", 1)
            trans_matches = pattern.findall(translated)

        for placeholder in orig_matches:
            if placeholder not in trans_matches:
                # Not found at all — append at the end
                translated = translated.rstrip() + " " + placeholder
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


def _save_context_cache(
    cache_path: Path,
    checked: set[str],
    changed: set[str],
    fixed_langs: set[str],
) -> None:
    """Persist fix_context progress for resume after cancellation."""
    import json as _json

    cache_path.write_text(
        _json.dumps(
            {
                "checked": list(checked),
                "changed": list(changed),
                "fixed_langs": list(fixed_langs),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class TranslationEngine:
    """Motor de tradução para projetos gettext."""

    def __init__(self, api_client: TranslationAPI, textdomain: str):
        self.api = api_client
        self.textdomain = textdomain

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
                    sub_fraction = done / total
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
                results[lang_code] = True

                if progress_callback:
                    progress_callback(
                        lang_code,
                        f"success: {strings_translated} strings",
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
        pot = polib.pofile(str(pot_file))

        # Provide app context to LLM-based APIs (name + sample strings)
        context_strings = [e.msgid for e in pot if e.msgid][:20]
        self.api.set_context(self.textdomain, context_strings)

        # Caminho do arquivo .po — derive from pot_file location
        locale_dir = pot_file.parent
        po_path = locale_dir / f"{lang}.po"

        # Cria ou carrega arquivo .po
        if po_path.exists():
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

        if force_retranslate:
            # Retranslate ALL entries (including already translated ones)
            entries_to_translate = [e for e in po if e.msgid]
        else:
            # Only untranslated + fuzzy
            entries_to_translate = list(po.untranslated_entries()) + list(
                po.fuzzy_entries()
            )

        # If fix_msgids is set, also include translated entries whose msgid is in that set
        if fix_msgids:
            existing_ids = {e.msgid for e in entries_to_translate}
            for entry in po.translated_entries():
                if entry.msgid in fix_msgids and entry.msgid not in existing_ids:
                    entries_to_translate.append(entry)

        translated_count = 0

        # Use batch translation when available (reduces API calls dramatically)
        batch_size = 15
        for batch_start in range(0, len(entries_to_translate), batch_size):
            if cancel_event and cancel_event.is_set():
                break

            # Respect API rate limits between outer batches
            if batch_start > 0 and self.api.batch_delay > 0:
                import time as _time

                _time.sleep(self.api.batch_delay)

            batch_entries = entries_to_translate[batch_start : batch_start + batch_size]
            batch_entries = [e for e in batch_entries if e.msgid]
            if not batch_entries:
                continue

            # Protect placeholders for all entries in the batch
            protected_texts = []
            token_maps = []
            for entry in batch_entries:
                protected, tokens = _protect_placeholders(entry.msgid)
                protected_texts.append(protected)
                token_maps.append(tokens)

            try:
                translations = self.api.translate_batch(
                    texts=protected_texts, source_lang="en", target_lang=lang
                )
            except Exception:
                # Batch failed — fall back to individual calls
                translations = []
                for text in protected_texts:
                    try:
                        translations.append(
                            self.api.translate(
                                text=text, source_lang="en", target_lang=lang
                            )
                        )
                    except Exception:
                        translations.append(None)

            for entry, translation, tokens in zip(
                batch_entries, translations, token_maps
            ):
                if translation is None:
                    if "fuzzy" not in entry.flags:
                        entry.flags.append("fuzzy")
                    if not entry.msgstr:
                        entry.msgstr = entry.msgid
                    continue

                # Restaura placeholders originais
                if tokens:
                    translation = _restore_placeholders(translation, tokens)

                # Reject implausible translations (batch shifting detection)
                if not _is_translation_plausible(entry.msgid, translation):
                    log.warning(
                        "Implausible translation rejected for '%s': '%s'",
                        entry.msgid[:40], translation[:40],
                    )
                    # Fall back to individual translation
                    try:
                        protected, single_tokens = _protect_placeholders(entry.msgid)
                        single_trans = self.api.translate(
                            text=protected, source_lang="en", target_lang=lang
                        )
                        if single_tokens:
                            single_trans = _restore_placeholders(single_trans, single_tokens)
                        translation = single_trans
                    except Exception:
                        translation = entry.msgid
                        if "fuzzy" not in entry.flags:
                            entry.flags.append("fuzzy")

                # Valida se placeholders estão intactos
                if not _validate_placeholders(entry.msgid, translation):
                    translation = _fix_placeholders(entry.msgid, translation)
                    if not _validate_placeholders(entry.msgid, translation):
                        translation = entry.msgid
                        if "fuzzy" not in entry.flags:
                            entry.flags.append("fuzzy")

                entry.msgstr = _match_newlines(entry.msgid, translation)
                if _validate_placeholders(entry.msgid, entry.msgstr):
                    if "fuzzy" in entry.flags:
                        entry.flags.remove("fuzzy")
                translated_count += 1

            # Emit detail pairs for live viewer
            if detail_callback:
                pairs = []
                for entry, translation in zip(batch_entries, translations):
                    if translation is not None:
                        pairs.append((entry.msgid, entry.msgstr, ""))
                if pairs:
                    detail_callback(pairs)

            if batch_progress:
                batch_progress(
                    min(batch_start + batch_size, len(entries_to_translate)),
                    len(entries_to_translate),
                )

        # Final validation pass — catch any remaining placeholder issues
        fixed_in_validation = 0
        for entry in po:
            if not entry.msgstr or entry.obsolete:
                continue
            if not _validate_placeholders(entry.msgid, entry.msgstr):
                repaired = _fix_placeholders(entry.msgid, entry.msgstr)
                if _validate_placeholders(entry.msgid, repaired):
                    entry.msgstr = repaired
                    fixed_in_validation += 1
                else:
                    # Cannot fix — revert to original and mark fuzzy
                    entry.msgstr = entry.msgid
                    if "fuzzy" not in entry.flags:
                        entry.flags.append("fuzzy")
                    fixed_in_validation += 1
        if fixed_in_validation:
            log.info("Post-save validation fixed %d entries in %s", fixed_in_validation, lang)

        # Save .po file
        po.save(str(po_path))
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
        import json as _json

        pot = polib.pofile(str(pot_file))
        context_strings = [e.msgid for e in pot if e.msgid][:20]
        self.api.set_context(self.textdomain, context_strings)

        locale_dir = pot_file.parent
        ref_po_path = locale_dir / f"{reference_lang}.po"
        cache_path = locale_dir / ".langforge_context_cache.json"
        log.info(
            "fix_context: ref_po_path=%s exists=%s", ref_po_path, ref_po_path.exists()
        )

        # Load resume cache (msgids already checked + changed)
        already_checked: set[str] = set()
        changed_msgids: set[str] = set()
        fixed_langs: set[str] = set()
        if cache_path.exists():
            try:
                cache = _json.loads(cache_path.read_text(encoding="utf-8"))
                already_checked = set(cache.get("checked", []))
                changed_msgids = set(cache.get("changed", []))
                fixed_langs = set(cache.get("fixed_langs", []))
                log.info(
                    "fix_context: resuming — %d checked, %d changed, %d langs fixed",
                    len(already_checked),
                    len(changed_msgids),
                    len(fixed_langs),
                )
            except Exception:
                pass

        # Phase 1: find entries that change when translated with context (batch)
        if ref_po_path.exists():
            ref_po = polib.pofile(str(ref_po_path))
            all_entries = [e for e in ref_po.translated_entries() if e.msgid]
            remaining = [e for e in all_entries if e.msgid not in already_checked]
            total_entries = len(all_entries)
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

            # Process in batches for efficiency
            batch_size = 15
            for batch_start in range(0, len(remaining), batch_size):
                if cancel_event and cancel_event.is_set():
                    break

                # Respect API rate limits between outer batches
                if batch_start > 0 and self.api.batch_delay > 0:
                    import time as _time

                    _time.sleep(self.api.batch_delay)

                batch_entries = remaining[batch_start : batch_start + batch_size]
                batch_entries = [e for e in batch_entries if e.msgid]
                if not batch_entries:
                    continue

                # Protect placeholders
                protected_texts = []
                token_maps = []
                for entry in batch_entries:
                    protected, tokens = _protect_placeholders(entry.msgid)
                    protected_texts.append(protected)
                    token_maps.append(tokens)

                try:
                    new_translations = self.api.translate_batch(
                        texts=protected_texts,
                        source_lang="en",
                        target_lang=reference_lang,
                    )
                except Exception:
                    # Batch failed — fall back to individual
                    new_translations = []
                    for text in protected_texts:
                        try:
                            new_translations.append(
                                self.api.translate(
                                    text=text,
                                    source_lang="en",
                                    target_lang=reference_lang,
                                )
                            )
                        except Exception as exc:
                            log.warning("fix_context: error: %s", exc)
                            new_translations.append(None)

                for entry, new_translation, tokens in zip(
                    batch_entries, new_translations, token_maps
                ):
                    checked += 1
                    already_checked.add(entry.msgid)

                    if new_translation is None:
                        continue

                    if tokens:
                        new_translation = _restore_placeholders(new_translation, tokens)

                    if new_translation.strip() != entry.msgstr.strip():
                        changed_msgids.add(entry.msgid)
                        log.info(
                            "fix_context: CHANGED '%s' old='%s' new='%s'",
                            entry.msgid[:40],
                            entry.msgstr[:40],
                            new_translation[:40],
                        )
                        entry.msgstr = _match_newlines(entry.msgid, new_translation)
                        if "fuzzy" in entry.flags:
                            entry.flags.remove("fuzzy")

                if progress_callback:
                    progress_callback(
                        reference_lang,
                        f"checking context: {checked}/{total_entries} "
                        f"({len(changed_msgids)} to fix)",
                        checked,
                        total_entries,
                    )

            ref_po.save(str(ref_po_path))

            # Save cache for resume
            _save_context_cache(
                cache_path, already_checked, changed_msgids, fixed_langs
            )

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
        lang_list = languages if languages else list(SUPPORTED_LANGUAGES.keys())
        # Remove reference language (already fixed) and already-fixed langs
        other_langs = [
            lc for lc in lang_list if lc != reference_lang and lc not in fixed_langs
        ]
        results: Dict[str, bool] = {reference_lang: True}
        # Total includes reference lang + already fixed + remaining
        already_fixed_count = len(fixed_langs)
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
        for done_lang in fixed_langs:
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
                results[lang] = True
                fixed_langs.add(lang)
                if progress_callback:
                    progress_callback(
                        lang,
                        f"success: fixed {len(changed_msgids)} entries",
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
            _save_context_cache(
                cache_path, already_checked, changed_msgids, fixed_langs
            )

        # If all languages completed, remove cache
        all_langs = set(languages if languages else SUPPORTED_LANGUAGES.keys())
        if fixed_langs | {reference_lang} >= all_langs:
            cache_path.unlink(missing_ok=True)

        return results

    def _create_metadata(self, lang: str) -> Dict[str, str]:
        """Cria metadata para arquivo .po."""
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
        }
