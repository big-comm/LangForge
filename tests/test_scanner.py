"""Tests for core.scanner project detection."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "usr" / "share" / "langforge"))

from core.scanner import ProjectScanner


class TestProjectScanner:
    def test_find_python_files(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "module.py").write_text("x = 1")
        (tmp_path / "readme.txt").write_text("not python")

        scanner = ProjectScanner(str(tmp_path))
        files = scanner.find_python_files()
        assert len(files) == 2
        assert all(f.suffix == ".py" for f in files)

    def test_find_python_files_empty_dir(self, tmp_path):
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.find_python_files() == []

    def test_find_python_files_nonexistent(self, tmp_path):
        import pytest

        scanner = ProjectScanner(str(tmp_path / "nonexistent"))
        with pytest.raises(FileNotFoundError):
            scanner.find_python_files()

    def test_detect_textdomain(self, tmp_path):
        (tmp_path / "app.py").write_text(
            'import gettext\ngettext.textdomain("myapp")\n'
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == "myapp"

    def test_detect_textdomain_fallback(self, tmp_path):
        (tmp_path / "app.py").write_text("print('no gettext')\n")
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == tmp_path.name

    def test_validate_project_with_gettext(self, tmp_path):
        (tmp_path / "app.py").write_text(
            'import gettext\nprint(_("Hello"))\n'
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is True

    def test_validate_project_without_gettext(self, tmp_path):
        (tmp_path / "app.py").write_text("print('no i18n')\n")
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is False

    def test_validate_empty_project(self, tmp_path):
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is False

    def test_count_translatable_strings(self, tmp_path):
        (tmp_path / "app.py").write_text(
            '_(\"Hello\")\n_(\"World\")\n_(\"Test\")\n'
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.count_translatable_strings() == 3

    def test_count_translatable_strings_none(self, tmp_path):
        (tmp_path / "app.py").write_text("print('no i18n')\n")
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.count_translatable_strings() == 0
