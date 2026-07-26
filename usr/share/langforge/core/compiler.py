"""Compilador de arquivos .po para .mo."""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Callable

from core.extractor import find_locale_directory
from utils.i18n import _


class MoCompiler:
    """Compilador de arquivos .po para .mo binários."""

    def __init__(self, project_path: Path, textdomain: str):
        self.project_path = Path(project_path).resolve(strict=True)
        self.textdomain = textdomain
        self.locale_dir = self._find_locale_dir()

    def _find_locale_dir(self) -> Path:
        """Find locale dir containing .po files, fallback to <root>/locale."""
        return find_locale_directory(self.project_path, self.textdomain)

    def compile_all(
        self, progress_callback: Optional[Callable[[str, str, int, int], None]] = None
    ) -> Dict[str, bool]:
        """
        Compila todos os arquivos .po para .mo.

        Args:
            progress_callback: Callback(lang, status, current, total)

        Returns:
            Dict com resultado de cada idioma {lang: success}
        """
        results = {}
        po_files = sorted(self.locale_dir.glob("*.po"))
        normalized: dict[str, Path] = {}
        for po_file in po_files:
            locale_code = po_file.stem.replace("-", "_")
            previous = normalized.get(locale_code)
            if previous is not None:
                raise RuntimeError(
                    _(
                        "Catalog names collide after locale normalization: "
                        "{first}, {second}"
                    ).format(
                        first=previous.name,
                        second=po_file.name,
                    )
                )
            normalized[locale_code] = po_file
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
            raise FileNotFoundError(_("Catalog not found: {path}").format(path=po_file))

        # Converte código do idioma para formato locale (pt-BR → pt_BR)
        locale_code = lang.replace("-", "_")
        collisions = sorted(
            candidate.name
            for candidate in self.locale_dir.glob("*.po")
            if candidate.stem.replace("-", "_") == locale_code
        )
        if len(collisions) > 1:
            raise RuntimeError(
                _("Catalog names collide after locale normalization: {names}").format(
                    names=", ".join(collisions)
                )
            )

        # Estrutura: usr/share/locale/{locale_code}/LC_MESSAGES/{textdomain}.mo
        mo_dir = (
            self.project_path / "usr" / "share" / "locale" / locale_code / "LC_MESSAGES"
        )
        mo_file = mo_dir / f"{self.textdomain}.mo"

        current_dir = self.project_path
        for part in ("usr", "share", "locale", locale_code, "LC_MESSAGES"):
            current_dir /= part
            if current_dir.is_symlink():
                raise RuntimeError(_("MO output directory contains a symlink"))
            current_dir.mkdir(exist_ok=True)

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.textdomain}.", suffix=".mo.tmp", dir=mo_dir
        )
        os.close(fd)
        try:
            subprocess.run(
                [
                    "msgfmt",
                    "--check",
                    "--check-format",
                    str(po_file),
                    "-o",
                    temporary_name,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            os.chmod(temporary_name, 0o644)
            with open(temporary_name, "rb") as temporary:
                os.fsync(temporary.fileno())
            os.replace(temporary_name, mo_file)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                _("Could not compile {lang}: {error}").format(
                    lang=lang,
                    error=e.stderr,
                )
            )
        except FileNotFoundError:
            raise RuntimeError(_("msgfmt was not found. Install the gettext package."))
        finally:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass

    def get_compiled_languages(self) -> list[str]:
        """Retorna lista de idiomas já compilados."""
        compiled: list[str] = []
        mo_base = self.project_path / "usr" / "share" / "locale"

        if not mo_base.exists():
            return compiled

        for lang_dir in mo_base.iterdir():
            if lang_dir.is_dir():
                mo_file = lang_dir / "LC_MESSAGES" / f"{self.textdomain}.mo"
                if mo_file.exists():
                    compiled.append(lang_dir.name)

        return compiled
