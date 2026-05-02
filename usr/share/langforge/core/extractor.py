"""Module for extracting translatable strings using xgettext."""

import logging
import subprocess
from pathlib import Path
from typing import List
import polib

log = logging.getLogger(__name__)

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
    ".rs": "Rust",
    ".vala": "Vala",
    ".ui": "Glade",
    ".blp": None,  # Blueprint needs blueprint-compiler, not xgettext
    ".sh": "Shell",
    ".bash": "Shell",
}

# Keywords common to most languages (function-style calls)
_BASE_KEYWORDS = [
    "_",
    "N_",
    "C_:1c,2",
    "gettext",
    "ngettext:1,2",
    "dgettext:2",
    "dcgettext:2",
    "pgettext:1c,2",
]

# Extra keywords for specific xgettext languages.
# Rust macros require the trailing `!` so xgettext recognises calls like
# `tr!("text")`; without it, macros are skipped entirely.
_LANG_EXTRA_KEYWORDS: dict[str, list[str]] = {
    "Rust": [
        "tr!",
        "trf!",
        "tr_n!:1,2",
        "gettext!",
        "ngettext!:1,2",
        "i18n!",
        "i18n_f!",
        "i18n_n!:1,2",
    ],
}


class GettextExtractor:
    """Wrapper for the xgettext command with multi-language support."""

    def __init__(self, project_path: str, textdomain: str):
        self.project_path = Path(project_path)
        if not textdomain or textdomain.startswith("."):
            textdomain = Path(project_path).name
        self.textdomain = textdomain
        self.locale_dir = self._find_locale_dir()
        self.pot_file = self.locale_dir / f"{textdomain}.pot"

    def _find_locale_dir(self) -> Path:
        """Find locale dir containing .pot/.po files, fallback to <root>/locale."""
        # check for existing .pot matching textdomain
        for pot in self.project_path.rglob(f"{self.textdomain}.pot"):
            if not pot.name.startswith("."):
                return pot.parent
        # check for any .po files
        for po in self.project_path.rglob("*.po"):
            return po.parent
        return self.project_path / "locale"

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

        log.info(
            "Extracting from %d files across %d languages: %s",
            sum(len(v) for v in lang_groups.values()),
            len(lang_groups),
            {k: len(v) for k, v in lang_groups.items()},
        )
        log.info("Locale dir: %s, .pot: %s", self.locale_dir, self.pot_file)

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
                keywords = _BASE_KEYWORDS + _LANG_EXTRA_KEYWORDS.get(lang, [])
                cmd = [
                    "xgettext",
                    f"--language={lang}",
                ]
                cmd += [f"--keyword={k}" for k in keywords]
                cmd += [
                    "--from-code=UTF-8",
                    "--add-comments",
                    "--force-po",
                    f"--output={tmp_pot}",
                    f"--package-name={self.textdomain}",
                    "--msgid-bugs-address=",
                ] + files

                result = subprocess.run(
                    cmd, check=True, capture_output=True, text=True
                )
                if result.stderr:
                    log.debug("xgettext (%s) stderr: %s", lang, result.stderr)
                if tmp_pot.exists():
                    temp_pots.append(tmp_pot)

            if not temp_pots:
                if self.pot_file.exists():
                    return True
                raise RuntimeError(
                    "xgettext could not write the .pot file. "
                    "Check write permissions on "
                    f"{self.locale_dir}"
                )

            if len(temp_pots) == 1:
                temp_pots[0].rename(self.pot_file)
            else:
                cmd = ["msgcat", "--use-first", f"--output={self.pot_file}"]
                cmd.extend(str(p) for p in temp_pots)
                subprocess.run(cmd, check=True, capture_output=True, text=True)

            if not self.pot_file.exists():
                return False

            # Verify the .pot has at least one translatable string.
            # With --force-po, xgettext writes a header-only .pot when no
            # gettext markers are found — surface that as a clear error
            # instead of silently producing an empty translation.
            try:
                pot = polib.pofile(str(self.pot_file))
                if not any(entry.msgid for entry in pot):
                    raise RuntimeError(
                        "No translatable strings found. Make sure your "
                        "source files use gettext markers like _(\"text\") "
                        "or gettext(\"text\")."
                    )
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(f"Failed to read generated .pot: {e}")

            return True

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
