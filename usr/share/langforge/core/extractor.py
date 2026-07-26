"""Module for extracting translatable strings using xgettext."""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List
import polib

from core.scanner import ProjectScanner

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
_LOCALE_DIR_NAMES = ("locale", "po", "locales", "translations")


def find_locale_directory(project_path: Path, textdomain: str) -> Path:
    """Find a safe project-owned gettext source directory."""
    project_root = Path(project_path).resolve(strict=True)
    project_files = ProjectScanner(str(project_root))._project_files()

    existing_directories = []
    for directory_name in _LOCALE_DIR_NAMES:
        directory = project_root / directory_name
        if directory.is_symlink():
            raise ValueError("Locale directory cannot be a symlink")
        if directory.is_dir():
            existing_directories.append(directory)
            has_catalog = any(
                path.parent == directory and path.suffix in {".po", ".pot"}
                for path in project_files
            )
            if has_catalog:
                return directory
            continue
        if directory.exists():
            raise ValueError("Locale path is not a directory")

    matching_templates = [
        path
        for path in project_files
        if path.name == f"{textdomain}.pot" and not path.name.startswith(".")
    ]
    if matching_templates:
        return min(
            matching_templates,
            key=lambda path: (
                len(path.relative_to(project_root).parts),
                path.relative_to(project_root).as_posix(),
            ),
        ).parent

    catalog_directories = {
        path.parent
        for path in project_files
        if path.suffix == ".po"
        and path.parent.name.casefold() in _LOCALE_DIR_NAMES
    }
    if catalog_directories:
        return min(
            catalog_directories,
            key=lambda path: (
                len(path.relative_to(project_root).parts),
                path.relative_to(project_root).as_posix(),
            ),
        )

    for directory in existing_directories:
        if directory.name in {"locale", "po"}:
            return directory

    locale_dir = project_root / "locale"
    if locale_dir.is_symlink():
        raise ValueError("Locale directory cannot be a symlink")
    return locale_dir


class GettextExtractor:
    """Wrapper for the xgettext command with multi-language support."""

    def __init__(self, project_path: str, textdomain: str):
        self.project_path = Path(project_path).resolve(strict=True)
        if (
            not textdomain
            or textdomain.startswith(".")
            or "/" in textdomain
            or "\\" in textdomain
            or "\0" in textdomain
        ):
            textdomain = self.project_path.name
        if not textdomain or textdomain.startswith("."):
            raise ValueError("Invalid gettext textdomain")
        self.textdomain = textdomain
        self.locale_dir = self._find_locale_dir()
        self.pot_file = self.locale_dir / f"{textdomain}.pot"

    def _find_locale_dir(self) -> Path:
        """Find locale dir containing .pot/.po files, fallback to <root>/locale."""
        return find_locale_directory(self.project_path, self.textdomain)

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

        try:
            # Keep all intermediate files in a private, unpredictable directory.
            # This avoids clobbering project files or following attacker-created
            # symlinks at the former predictable .tmp_<language>.pot paths.
            with tempfile.TemporaryDirectory(
                prefix=".langforge-", dir=self.locale_dir
            ) as temp_dir:
                work_dir = Path(temp_dir)
                temp_pots: list[Path] = []

                for index, (lang, files) in enumerate(lang_groups.items()):
                    tmp_pot = work_dir / f"{index}.pot"
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
                    generated_pot = temp_pots[0]
                else:
                    generated_pot = work_dir / "merged.pot"
                    cmd = ["msgcat", "--use-first", f"--output={generated_pot}"]
                    cmd.extend(str(p) for p in temp_pots)
                    subprocess.run(cmd, check=True, capture_output=True, text=True)

                if not generated_pot.exists():
                    return False

                # Validate before replacing an existing catalog.
                try:
                    pot = polib.pofile(str(generated_pot))
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

                generated_pot.replace(self.pot_file)
                return True

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"xgettext error: {e.stderr}")
        except FileNotFoundError:
            raise RuntimeError("xgettext not found. Install the gettext package.")

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
