"""Module for scanning projects and detecting gettext usage."""

import re
from pathlib import Path
from typing import List

# Source file extensions to scan for gettext usage
_SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",  # Python, JavaScript, TypeScript
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",  # C/C++
    ".vala",
    ".ui",
    ".blp",  # Vala, GTK UI files
    ".sh",
    ".bash",  # Shell scripts
}

# Patterns that indicate gettext usage across different languages
_GETTEXT_PATTERNS = [
    # Python
    re.compile(r"import\s+gettext"),
    re.compile(r"from\s+gettext\s+import"),
    # JavaScript / GNOME Shell extensions
    re.compile(r"gettext\s+as\s+_"),
    re.compile(r"imports\.gettext"),
    re.compile(r"Gettext\.gettext"),
    re.compile(r"GLib\.dgettext"),
    # C / Vala
    re.compile(r'#\s*include\s+[<"].*gettext\.h[>"]'),
    re.compile(r"\b[dn]?gettext\s*\("),
    re.compile(r"\bN?_\s*\("),
    # Shell
    re.compile(r"\$\(\s*gettext\b"),
    re.compile(r"eval_gettext"),
    # Generic _() usage (common across all)
    re.compile(r'_\s*\(\s*["\']'),
]

# Patterns for detecting textdomain declarations across languages
_TEXTDOMAIN_PATTERNS = [
    # Python: gettext.textdomain("name")
    re.compile(r'gettext\.textdomain\s*\(\s*["\']([^"\']+)["\']\s*\)'),
    # C/Vala: textdomain("name") or GETTEXT_PACKAGE
    re.compile(r'textdomain\s*\(\s*["\']([^"\']+)["\']\s*\)'),
    re.compile(r'GETTEXT_PACKAGE\s*=\s*["\']([^"\']+)["\']'),
    # JavaScript: Extension metadata or GLib.textdomain
    re.compile(r'textdomain\s*[=:]\s*["\']([^"\']+)["\']'),
    # Meson build: i18n definition
    re.compile(r"i18n\.gettext\s*\(\s*'([^']+)'"),
    # Shell: TEXTDOMAIN=name
    re.compile(r'TEXTDOMAIN\s*=\s*["\']?([^\s"\']+)'),
]


class ProjectScanner:
    """Project scanner with multi-language gettext detection."""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)

    def find_source_files(self) -> List[Path]:
        """Find all source files that could use gettext."""
        if not self.project_path.exists():
            raise FileNotFoundError(f"Directory not found: {self.project_path}")

        files = []
        for ext in _SOURCE_EXTENSIONS:
            files.extend(self.project_path.rglob(f"*{ext}"))
        return files

    def find_python_files(self) -> List[Path]:
        """Find all .py files recursively (kept for compatibility)."""
        if not self.project_path.exists():
            raise FileNotFoundError(f"Directory not found: {self.project_path}")
        return list(self.project_path.rglob("*.py"))

    def _find_pot_files(self) -> List[Path]:
        """Find .pot template files in the project."""
        return list(self.project_path.rglob("*.pot"))

    def _find_po_files(self) -> List[Path]:
        """Find .po translation files in the project."""
        return list(self.project_path.rglob("*.po"))

    def detect_textdomain(self) -> str:
        """Detect the project's textdomain name.

        Checks (in priority order):
        1. .pot filename (most reliable)
        2. textdomain declarations in source files
        3. Directory name as fallback
        """
        # Priority 1: .pot filename
        pot_files = self._find_pot_files()
        if pot_files:
            return pot_files[0].stem

        # Priority 2: textdomain in source code
        for src_file in self.find_source_files():
            if src_file.name == "scanner.py":
                continue
            try:
                content = src_file.read_text(encoding="utf-8")
                for pattern in _TEXTDOMAIN_PATTERNS:
                    match = pattern.search(content)
                    if match:
                        return match.group(1)
            except Exception:
                continue

        # Fallback: directory name
        return self.project_path.name

    def validate_project(self) -> bool:
        """Check if this is a valid project with gettext.

        Detection strategy (any match is sufficient):
        1. .pot file exists (strongest signal)
        2. .po files exist
        3. Source files contain gettext patterns
        """
        # Check for .pot or .po files first (strongest indicator)
        if self._find_pot_files() or self._find_po_files():
            return True

        # Check source files for gettext patterns
        for src_file in self.find_source_files():
            try:
                content = src_file.read_text(encoding="utf-8")
                if any(p.search(content) for p in _GETTEXT_PATTERNS):
                    return True
            except Exception:
                continue

        return False

    def count_translatable_strings(self) -> int:
        """Count approximately how many strings are translatable.

        If a .pot file exists, count its entries for accuracy.
        Otherwise, scan source files for _() patterns.
        """
        # Prefer .pot count (most accurate)
        pot_files = self._find_pot_files()
        if pot_files:
            try:
                import polib

                po = polib.pofile(str(pot_files[0]))
                return len([e for e in po if not e.obsolete])
            except Exception:
                pass

        # Fallback: regex count across source files
        pattern = re.compile(r'_\s*\(\s*["\']([^"\']+)["\']\s*\)')
        count = 0
        for src_file in self.find_source_files():
            try:
                content = src_file.read_text(encoding="utf-8")
                count += len(pattern.findall(content))
            except Exception:
                continue
        return count
