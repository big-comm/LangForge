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


class TestMultiLanguageDetection:
    """Tests for JavaScript, C, Vala, Shell gettext detection."""

    def test_find_source_files_js(self, tmp_path):
        (tmp_path / "ext.js").write_text("const x = 1;")
        (tmp_path / "style.css").write_text("body {}")
        scanner = ProjectScanner(str(tmp_path))
        files = scanner.find_source_files()
        assert len(files) == 1
        assert files[0].suffix == ".js"

    def test_find_source_files_mixed(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1")
        (tmp_path / "ext.js").write_text("var a;")
        (tmp_path / "lib.c").write_text("int main() {}")
        (tmp_path / "readme.md").write_text("docs")
        scanner = ProjectScanner(str(tmp_path))
        files = scanner.find_source_files()
        exts = {f.suffix for f in files}
        assert exts == {".py", ".js", ".c"}

    def test_validate_js_gettext(self, tmp_path):
        (tmp_path / "extension.js").write_text(
            "import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';\n"
            "const label = _('Hello');\n"
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is True

    def test_validate_c_gettext(self, tmp_path):
        (tmp_path / "main.c").write_text(
            '#include <libintl.h>\n#include "gettext.h"\nprintf(gettext("Hello"));\n'
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is True

    def test_validate_shell_gettext(self, tmp_path):
        (tmp_path / "script.sh").write_text('#!/bin/bash\necho $(gettext "Hello")\n')
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is True

    def test_validate_vala_gettext(self, tmp_path):
        (tmp_path / "app.vala").write_text('var label = _("Hello World");\n')
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is True

    def test_validate_pot_file_presence(self, tmp_path):
        """A .pot file alone should be enough to validate."""
        locale = tmp_path / "locale"
        locale.mkdir()
        (locale / "myapp.pot").write_text('# POT file\nmsgid "Hello"\nmsgstr ""\n')
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is True

    def test_validate_po_file_presence(self, tmp_path):
        """Existing .po files should validate the project."""
        locale = tmp_path / "locale"
        locale.mkdir()
        (locale / "pt.po").write_text('msgid "Hello"\nmsgstr "Olá"\n')
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.validate_project() is True

    def test_detect_textdomain_from_pot(self, tmp_path):
        """Textdomain should be detected from .pot filename."""
        locale = tmp_path / "locale"
        locale.mkdir()
        (locale / "gnome-shell-big-shot.pot").write_text("# pot\n")
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == "gnome-shell-big-shot"

    def test_detect_textdomain_js_metadata(self, tmp_path):
        (tmp_path / "metadata.json").write_text("{}")
        (tmp_path / "prefs.js").write_text('const textdomain = "my-extension";\n')
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == "my-extension"

    def test_detect_textdomain_meson(self, tmp_path):
        (tmp_path / "meson.build").write_text("i18n.gettext('cool-app')\n")
        # meson.build is not in _SOURCE_EXTENSIONS, so this tests fallback
        scanner = ProjectScanner(str(tmp_path))
        # Falls back to directory name since meson.build isn't scanned
        assert scanner.detect_textdomain() == tmp_path.name

    def test_count_strings_from_pot(self, tmp_path):
        """count_translatable_strings should prefer .pot entry count."""
        locale = tmp_path / "locale"
        locale.mkdir()
        pot_content = (
            '#\nmsgid ""\nmsgstr ""\n\n'
            'msgid "Hello"\nmsgstr ""\n\n'
            'msgid "World"\nmsgstr ""\n\n'
            'msgid "Test"\nmsgstr ""\n'
        )
        (locale / "app.pot").write_text(pot_content)
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.count_translatable_strings() == 3

    def test_count_strings_from_js_source(self, tmp_path):
        (tmp_path / "app.js").write_text(
            'const a = _("Hello");\nconst b = _("World");\n'
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.count_translatable_strings() == 2
