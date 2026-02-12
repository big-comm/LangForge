"""Compilador de arquivos .po para .mo."""

import subprocess
from pathlib import Path
from typing import Dict, Optional, Callable

from core.languages import SUPPORTED_LANGUAGES


class MoCompiler:
    """Compilador de arquivos .po para .mo binários."""

    def __init__(self, project_path: Path, textdomain: str):
        self.project_path = Path(project_path)
        self.textdomain = textdomain
        self.locale_dir = self.project_path / "locale"

    def compile_all(
        self,
        progress_callback: Optional[Callable[[str, str, int, int], None]] = None
    ) -> Dict[str, bool]:
        """
        Compila todos os arquivos .po para .mo.

        Args:
            progress_callback: Callback(lang, status, current, total)

        Returns:
            Dict com resultado de cada idioma {lang: success}
        """
        results = {}
        po_files = list(self.locale_dir.glob("*.po"))
        total = len(po_files)
        current = 0

        for po_file in po_files:
            lang = po_file.stem
            current += 1

            try:
                self.compile_language(lang)
                results[lang] = True

                if progress_callback:
                    progress_callback(lang, "compiled", current, total)
            except Exception as e:
                results[lang] = False
                if progress_callback:
                    progress_callback(lang, f"error: {e}", current, total)

        return results

    def compile_language(self, lang: str):
        """
        Compila .po → .mo e coloca na estrutura correta do sistema.

        Args:
            lang: Código do idioma (ex: 'pt-BR')
        """
        # Caminhos
        po_file = self.locale_dir / f"{lang}.po"
        if not po_file.exists():
            raise FileNotFoundError(f"Arquivo {po_file} não encontrado")

        # Converte código do idioma para formato locale (pt-BR → pt_BR)
        locale_code = lang.replace("-", "_")

        # Estrutura: usr/share/locale/{locale_code}/LC_MESSAGES/{textdomain}.mo
        mo_dir = self.project_path / "usr" / "share" / "locale" / locale_code / "LC_MESSAGES"
        mo_file = mo_dir / f"{self.textdomain}.mo"

        # Cria diretórios
        mo_dir.mkdir(parents=True, exist_ok=True)

        # Compila com msgfmt
        try:
            subprocess.run(
                ["msgfmt", str(po_file), "-o", str(mo_file)],
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Erro ao compilar {lang}: {e.stderr}")
        except FileNotFoundError:
            raise RuntimeError("msgfmt não encontrado. Instale o pacote gettext.")

    def get_compiled_languages(self) -> list[str]:
        """Retorna lista de idiomas já compilados."""
        compiled = []
        mo_base = self.project_path / "usr" / "share" / "locale"

        if not mo_base.exists():
            return compiled

        for lang_dir in mo_base.iterdir():
            if lang_dir.is_dir():
                mo_file = lang_dir / "LC_MESSAGES" / f"{self.textdomain}.mo"
                if mo_file.exists():
                    compiled.append(lang_dir.name)

        return compiled
