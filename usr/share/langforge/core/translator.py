"""Motor de tradução que coordena APIs e arquivos .po."""

import polib
from pathlib import Path
from typing import Dict, Callable, Optional
from datetime import datetime

from core.languages import SUPPORTED_LANGUAGES
from api.base import TranslationAPI


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
                translation = self.api.translate(
                    text=entry.msgid,
                    source_lang='en',
                    target_lang=lang
                )
                entry.msgstr = translation
                # Remove fuzzy flag if it was there
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
