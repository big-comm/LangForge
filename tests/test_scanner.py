"""Tests for core.scanner project detection."""

import sys
from pathlib import Path

import pytest

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
        scanner = ProjectScanner(str(tmp_path / "nonexistent"))
        with pytest.raises(FileNotFoundError):
            scanner.find_python_files()

    def test_discovery_skips_generated_vendor_and_vcs_trees(self, tmp_path):
        source = tmp_path / "main.py"
        source.write_text("print('source')\n")
        for dirname in ("build", "vendor", ".git", ".hg", ".svn", "node_modules"):
            ignored = tmp_path / dirname
            ignored.mkdir()
            (ignored / "ignored.py").write_text('_("Ignored")\n')
            (ignored / f"{dirname.strip('.')}.pot").write_text(
                'msgid "Ignored"\nmsgstr ""\n'
            )

        scanner = ProjectScanner(str(tmp_path))

        assert scanner.find_source_files() == [source]
        assert scanner.find_python_files() == [source]
        assert scanner.validate_project() is False
        assert scanner.count_translatable_strings() == 0
        assert scanner.detect_textdomain() == tmp_path.name

    def test_external_symlinks_do_not_escape_project(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_source = outside / "outside.py"
        outside_source.write_text(
            'import gettext\ngettext.textdomain("outside")\n_("Outside")\n'
        )
        outside_pot = outside / "outside.pot"
        outside_pot.write_text('msgid "Outside"\nmsgstr ""\n')

        (project / "linked.py").symlink_to(outside_source)
        (project / "linked.pot").symlink_to(outside_pot)
        (project / "linked-directory").symlink_to(outside, target_is_directory=True)
        scanner = ProjectScanner(str(project))

        assert scanner.find_source_files() == []
        assert scanner.validate_project() is False
        assert scanner.count_translatable_strings() == 0
        assert scanner.detect_textdomain() == "project"

    def test_detect_textdomain(self, tmp_path):
        (tmp_path / "app.py").write_text(
            'import gettext\ngettext.textdomain("myapp")\n'
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == "myapp"

    def test_python_declaration_detection_ignores_string_examples(self, tmp_path):
        (tmp_path / "app.py").write_text(
            "EXAMPLE = 'gettext.textdomain(\"not-the-domain\")'\n"
            "import gettext\n"
            'gettext.textdomain("actual-domain")\n'
        )

        scanner = ProjectScanner(str(tmp_path))

        assert scanner.detect_textdomain() == "actual-domain"

    def test_detect_textdomain_fallback(self, tmp_path):
        (tmp_path / "app.py").write_text("print('no gettext')\n")
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == tmp_path.name

    def test_validate_project_with_gettext(self, tmp_path):
        (tmp_path / "app.py").write_text('import gettext\nprint(_("Hello"))\n')
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
        (tmp_path / "app.py").write_text('_("Hello")\n_("World")\n_("Test")\n')
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

    def test_explicit_textdomain_takes_priority_over_pot(self, tmp_path):
        locale = tmp_path / "locale"
        locale.mkdir()
        (locale / "stale.pot").write_text("# stale\n")
        (tmp_path / "app.py").write_text(
            'import gettext\ngettext.textdomain("canonical")\n'
        )

        scanner = ProjectScanner(str(tmp_path))

        assert scanner.detect_textdomain() == "canonical"

    def test_multiple_pots_without_declaration_are_rejected_deterministically(
        self, tmp_path
    ):
        locale = tmp_path / "locale"
        locale.mkdir()
        (locale / "zeta.pot").write_text("# zeta\n")
        (locale / "alpha.pot").write_text("# alpha\n")

        scanner = ProjectScanner(str(tmp_path))

        with pytest.raises(
            ValueError,
            match=r"Multiple gettext templates found \(alpha, zeta\)",
        ):
            scanner.detect_textdomain()
        with pytest.raises(ValueError):
            scanner.count_translatable_strings()

    def test_detect_textdomain_js_metadata(self, tmp_path):
        (tmp_path / "metadata.json").write_text("{}")
        (tmp_path / "prefs.js").write_text('const textdomain = "my-extension";\n')
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == "my-extension"

    def test_detect_textdomain_meson(self, tmp_path):
        (tmp_path / "meson.build").write_text("i18n.gettext('cool-app')\n")
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.detect_textdomain() == "cool-app"

    def test_commented_c_textdomain_does_not_create_conflict(self, tmp_path):
        (tmp_path / "app.c").write_text(
            'bindtextdomain("real-app", "/usr/share/locale");\n'
            '// bindtextdomain("retired-app", "/tmp");\n'
            '/* textdomain("also-retired"); */\n',
            encoding="utf-8",
        )

        scanner = ProjectScanner(str(tmp_path))

        assert scanner.detect_textdomain() == "real-app"

    def test_commented_gettext_calls_are_not_counted_as_usage(self, tmp_path):
        (tmp_path / "app.c").write_text(
            '// _("retired")\n/* gettext("also retired") */\n',
            encoding="utf-8",
        )
        scanner = ProjectScanner(str(tmp_path))

        assert scanner.validate_project() is False
        assert scanner.count_translatable_strings() == 0

    def test_python_comments_and_docstrings_are_not_gettext_usage(self, tmp_path):
        (tmp_path / "app.py").write_text(
            '# _("commented")\n'
            '"""Documentation mentioning _("not a call")."""\n',
            encoding="utf-8",
        )
        scanner = ProjectScanner(str(tmp_path))

        assert scanner.validate_project() is False
        assert scanner.count_translatable_strings() == 0

    def test_python2_syntax_uses_token_fallback(self, tmp_path):
        (tmp_path / "app.py").write_text(
            'import gettext\n'
            'gettext.textdomain("legacy-app")\n'
            'print _("Hello")\n'
            'print ngettext("One file", "Many files", count)\n',
            encoding="utf-8",
        )
        scanner = ProjectScanner(str(tmp_path))

        assert scanner.validate_project() is True
        assert scanner.detect_textdomain() == "legacy-app"
        assert scanner.count_translatable_strings() == 2

    def test_python2_token_fallback_ignores_comments_and_string_examples(
        self, tmp_path
    ):
        (tmp_path / "app.py").write_text(
            'import gettext\n'
            '# gettext.textdomain("commented-domain")\n'
            'EXAMPLE = \'gettext.textdomain("string-domain")\'\n'
            'gettext.textdomain("legacy-app")\n'
            '# _("commented")\n'
            'print _("Real")\n',
            encoding="utf-8",
        )
        scanner = ProjectScanner(str(tmp_path))

        assert scanner.detect_textdomain() == "legacy-app"
        assert scanner.count_translatable_strings() == 1

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

    def test_count_uses_pot_matching_explicit_textdomain(self, tmp_path):
        locale = tmp_path / "locale"
        locale.mkdir()
        (locale / "alpha.pot").write_text(
            'msgid ""\nmsgstr ""\n\nmsgid "Alpha"\nmsgstr ""\n'
        )
        (locale / "beta.pot").write_text(
            'msgid ""\nmsgstr ""\n\n'
            'msgid "Beta one"\nmsgstr ""\n\n'
            'msgid "Beta two"\nmsgstr ""\n'
        )
        (tmp_path / "app.py").write_text('import gettext\ngettext.textdomain("beta")\n')

        scanner = ProjectScanner(str(tmp_path))

        assert scanner.count_translatable_strings() == 2

    def test_count_strings_from_js_source(self, tmp_path):
        (tmp_path / "app.js").write_text(
            'const a = _("Hello");\nconst b = _("World");\n'
        )
        scanner = ProjectScanner(str(tmp_path))
        assert scanner.count_translatable_strings() == 2
