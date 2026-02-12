"""Module for scanning Python projects and detecting gettext."""

import re
from pathlib import Path
from typing import List


class ProjectScanner:
    """Python project scanner with gettext support."""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)

    def find_python_files(self) -> List[Path]:
        """Find all .py files recursively."""
        if not self.project_path.exists():
            raise FileNotFoundError(f"Directory not found: {self.project_path}")

        return list(self.project_path.rglob("*.py"))

    def detect_textdomain(self) -> str:
        """
        Detect the project's textdomain name.
        Searches for gettext.textdomain("name") in files.
        """
        # Pattern: gettext.textdomain("name") or textdomain("name")
        # Must be actual code, not in comments
        pattern = re.compile(r'^[^#]*gettext\.textdomain\s*\(\s*["\']([^"\']+)["\']\s*\)', re.MULTILINE)

        for py_file in self.find_python_files():
            # Skip scanner.py itself to avoid matching example in docstring
            if py_file.name == 'scanner.py':
                continue
            try:
                content = py_file.read_text(encoding='utf-8')
                match = pattern.search(content)
                if match:
                    return match.group(1)
            except Exception:
                continue

        # Fallback: use directory name
        return self.project_path.name

    def validate_project(self) -> bool:
        """
        Check if this is a valid project with gettext.
        Looks for gettext imports and _() usage.
        """
        gettext_patterns = [
            re.compile(r'import\s+gettext'),
            re.compile(r'from\s+gettext\s+import'),
            re.compile(r'_\s*\('),  # _() function
        ]

        python_files = self.find_python_files()
        if not python_files:
            return False

        for py_file in python_files:
            try:
                content = py_file.read_text(encoding='utf-8')
                if any(pattern.search(content) for pattern in gettext_patterns):
                    return True
            except Exception:
                continue

        return False

    def count_translatable_strings(self) -> int:
        """Count approximately how many strings are translatable."""
        pattern = re.compile(r'_\s*\(["\']([^"\']+)["\']\)')
        count = 0

        for py_file in self.find_python_files():
            try:
                content = py_file.read_text(encoding='utf-8')
                count += len(pattern.findall(content))
            except Exception:
                continue

        return count
