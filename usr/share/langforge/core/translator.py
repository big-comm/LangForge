"""Motor de tradução que coordena APIs e arquivos .po."""

import re
import polib
from pathlib import Path
from typing import Dict, List, Tuple, Callable, Optional
from datetime import datetime

from core.languages import SUPPORTED_LANGUAGES
from api.base import TranslationAPI

# Padrões de formato Python que devem ser preservados durante a tradução
_FORMAT_PATTERNS = [
    re.compile(r'%\([^)]+\)[sdifcr]'),  # %(name)s, %(count)d
    re.compile(r'%[sdifcr%]'),            # %s, %d, %i, %f, %c, %r, %%
    re.compile(r'\{[^}]*\}'),             # {}, {0}, {name}, {count:.2f}
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


def _restore_placeholders(text: str, tokens: List[Tuple[str, str]]) -> str:
    """Restaura placeholders originais a partir dos tokens XML."""
    for token, original in tokens:
        # Tenta também variações comuns de corrupção de tags
        text = text.replace(token, original)
        # APIs podem adicionar espaços extras: <x1 /> ou < x1/>
        text = text.replace(token.replace("/>", " />"), original)
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

    Se um placeholder do original está ausente na tradução,
    tenta inserí-lo de volta na posição mais provável.
    """
    for pattern in _FORMAT_PATTERNS:
        orig_matches = pattern.findall(original)
        trans_matches = pattern.findall(translated)
        for placeholder in orig_matches:
            if placeholder not in trans_matches:
                # Placeholder ausente - tenta inserir onde faria sentido
                # Procura por versões corrompidas (ex: {palavra) e substitui
                corrupted = re.compile(
                    re.escape(placeholder[0]) + r'[^}\s]*(?!\})'
                )
                match = corrupted.search(translated)
                if match:
                    translated = translated[:match.start()] + placeholder + translated[match.end():]
                else:
                    # Não encontrou versão corrompida, adiciona ao final
                    translated = translated.rstrip() + ' ' + placeholder
    return translated


class TranslationEngine:
    """Motor de tradução para projetos gettext."""

    def __init__(self, api_client: TranslationAPI, textdomain: str):
        self.api = api_client
        self.textdomain = textdomain

    def translate_project(
        self,
        pot_file: Path,
        project_path: Path,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None
    ) -> Dict[str, bool]:
        """
        Traduz projeto para todos os 29 idiomas.

        Args:
            pot_file: Arquivo .pot template
            project_path: Diretório do projeto
            progress_callback: Callback(lang_code, status, current, total)

        Returns:
            Dict com resultado de cada idioma {lang: success}
        """
        results = {}
        total_langs = len(SUPPORTED_LANGUAGES)
        current = 0

        for lang_code in SUPPORTED_LANGUAGES:
            current += 1
            try:
                strings_translated = self.translate_language(pot_file, lang_code, project_path)
                results[lang_code] = True

                if progress_callback:
                    progress_callback(
                        lang_code,
                        f"success: {strings_translated} strings",
                        current,
                        total_langs
                    )
            except Exception as e:
                results[lang_code] = False
                if progress_callback:
                    progress_callback(lang_code, f"error: {e}", current, total_langs)

        return results

    def translate_language(self, pot_file: Path, lang: str, project_path: Path) -> int:
        """
        Traduz um idioma específico.

        Args:
            pot_file: Arquivo .pot template
            lang: Código do idioma (ex: 'pt-BR')
            project_path: Diretório do projeto

        Returns:
            Número de strings traduzidas
        """
        # Carrega template .pot
        pot = polib.pofile(str(pot_file))

        # Caminho do arquivo .po
        locale_dir = project_path / "locale"
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

        # Get entries that need translation:
        # 1. Untranslated entries (empty msgstr)
        # 2. Fuzzy entries (marked for review - likely failed before)
        entries_to_translate = list(po.untranslated_entries()) + list(po.fuzzy_entries())

        translated_count = 0
        for entry in entries_to_translate:
            if not entry.msgid:
                continue

            try:
                # Protege placeholders de formato antes de enviar à API
                protected_text, tokens = _protect_placeholders(entry.msgid)

                translation = self.api.translate(
                    text=protected_text,
                    source_lang='en',
                    target_lang=lang
                )

                # Restaura placeholders originais
                if tokens:
                    translation = _restore_placeholders(translation, tokens)

                # Valida se placeholders estão intactos
                if not _validate_placeholders(entry.msgid, translation):
                    # Tenta reparar automaticamente
                    translation = _fix_placeholders(entry.msgid, translation)

                    # Valida novamente após reparo
                    if not _validate_placeholders(entry.msgid, translation):
                        # Não conseguiu reparar - copia original como fallback
                        translation = entry.msgid
                        if 'fuzzy' not in entry.flags:
                            entry.flags.append('fuzzy')

                entry.msgstr = translation
                # Remove fuzzy flag apenas se validação passou
                if _validate_placeholders(entry.msgid, translation):
                    if 'fuzzy' in entry.flags:
                        entry.flags.remove('fuzzy')
                translated_count += 1
            except Exception as e:
                # If translation fails, mark as fuzzy for manual review
                if 'fuzzy' not in entry.flags:
                    entry.flags.append('fuzzy')
                if not entry.msgstr:
                    entry.msgstr = entry.msgid  # Copy original as placeholder

        # Save .po file
        po.save(str(po_path))
        return translated_count

    def _create_metadata(self, lang: str) -> Dict[str, str]:
        """Cria metadata para arquivo .po."""
        return {
            'Project-Id-Version': self.textdomain,
            'Report-Msgid-Bugs-To': '',
            'POT-Creation-Date': datetime.now().strftime('%Y-%m-%d %H:%M%z'),
            'PO-Revision-Date': datetime.now().strftime('%Y-%m-%d %H:%M%z'),
            'Last-Translator': 'Translation Automator <auto@translator.ai>',
            'Language-Team': f'{SUPPORTED_LANGUAGES[lang]} <{lang}@li.org>',
            'Language': lang,
            'MIME-Version': '1.0',
            'Content-Type': 'text/plain; charset=UTF-8',
            'Content-Transfer-Encoding': '8bit',
        }
