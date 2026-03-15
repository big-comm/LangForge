"""Module for extracting translatable strings using xgettext."""

import subprocess
from pathlib import Path
from typing import List
import polib

# Mapping from file extension to xgettext --language value
_XGETTEXT_LANG_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "JavaScript",
    ".jsx": "JavaScript",
    ".tsx": "JavaScript",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cc": "C++",
    ".vala": "Vala",
    ".ui": "Glade",
    ".blp": None,  # Blueprint needs blueprint-compiler, not xgettext
    ".sh": "Shell",
    ".bash": "Shell",
}


class GettextExtractor:
    """Wrapper for the xgettext command with multi-language support."""

    def __init__(self, project_path: str, textdomain: str):
        self.project_path = Path(project_path)
        self.textdomain = textdomain
        self.locale_dir = self.project_path / "locale"
        self.pot_file = self.locale_dir / f"{textdomain}.pot"

    def extract_strings(self, source_files: List[Path]) -> bool:
        """Run xgettext to generate the .pot file.

        Groups files by xgettext language and merges results.
        If a .pot already exists and no source files are provided,
        the existing .pot is reused.

        Args:
            source_files: List of source files to extract strings from

        Returns:
            True if extraction was successful
        """
        self.locale_dir.mkdir(parents=True, exist_ok=True)

        # If .pot already exists and no extractable source files, reuse it
        if not source_files and self.pot_file.exists():
            return True

        if not source_files:
            raise ValueError("No source files provided for extraction")

        # Group files by xgettext language
        lang_groups: dict[str, list[str]] = {}
        for f in source_files:
            lang = _XGETTEXT_LANG_MAP.get(f.suffix)
            if lang is None:
                continue
            lang_groups.setdefault(lang, []).append(str(f))

        if not lang_groups:
            # No extractable files but .pot may exist from external tool
            if self.pot_file.exists():
                return True
            raise ValueError("No extractable source files found")

        # Extract per language into temp files, then merge
        temp_pots: list[Path] = []
        try:
            for lang, files in lang_groups.items():
                tmp_pot = self.locale_dir / f".tmp_{lang.lower()}.pot"
                cmd = [
                    "xgettext",
                    f"--language={lang}",
                    "--keyword=_",
                    "--keyword=N_",
                    "--keyword=C_:1c,2",
                    "--keyword=ngettext:1,2",
                    "--from-code=UTF-8",
                    "--add-comments",
                    f"--output={tmp_pot}",
                    f"--package-name={self.textdomain}",
                    "--msgid-bugs-address=",
                ] + files

                subprocess.run(cmd, check=True, capture_output=True, text=True)
                if tmp_pot.exists():
                    temp_pots.append(tmp_pot)

            if not temp_pots:
                if self.pot_file.exists():
                    return True
                raise RuntimeError("xgettext produced no output")

            if len(temp_pots) == 1:
                # Single language — just rename
                temp_pots[0].rename(self.pot_file)
            else:
                # Merge multiple .pot files with msgcat
                cmd = ["msgcat", "--use-first", f"--output={self.pot_file}"]
                cmd.extend(str(p) for p in temp_pots)
                subprocess.run(cmd, check=True, capture_output=True, text=True)

            return self.pot_file.exists()

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"xgettext error: {e.stderr}")
        except FileNotFoundError:
            raise RuntimeError("xgettext not found. Install the gettext package.")
        finally:
            # Clean up temp files
            for tmp in temp_pots:
                tmp.unlink(missing_ok=True)

    def get_extracted_strings(self) -> List[str]:
        """
        Lê o arquivo .pot e retorna lista de msgids.

        Returns:
            Lista de strings extraídas
        """
        if not self.pot_file.exists():
            return []

        try:
            pot = polib.pofile(str(self.pot_file))
            return [entry.msgid for entry in pot if entry.msgid]
        except Exception as e:
            raise RuntimeError(f"Erro ao ler .pot: {e}")

    def get_string_count(self) -> int:
        """Retorna número de strings extraídas."""
        return len(self.get_extracted_strings())
