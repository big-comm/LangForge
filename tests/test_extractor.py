"""Tests for safe gettext extraction."""

import subprocess
from pathlib import Path

import pytest

from core.extractor import GettextExtractor


_VALID_POT = (
    'msgid ""\n'
    'msgstr ""\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    "\n"
    'msgid "Hello"\n'
    'msgstr ""\n'
)


def _fake_gettext(cmd, **_kwargs):
    output_arg = next(arg for arg in cmd if arg.startswith("--output="))
    Path(output_arg.removeprefix("--output=")).write_text(
        _VALID_POT, encoding="utf-8"
    )
    return subprocess.CompletedProcess(cmd, 0, "", "")


def _source_file(project: Path, suffix: str = ".py") -> Path:
    source = project / f"app{suffix}"
    source.write_text('_("Hello")\n', encoding="utf-8")
    return source


def test_extract_does_not_clobber_legacy_temp_file(tmp_path, monkeypatch):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    sentinel = locale_dir / ".tmp_python.pot"
    sentinel.write_text("USER DATA", encoding="utf-8")
    monkeypatch.setattr("core.extractor.subprocess.run", _fake_gettext)

    extractor = GettextExtractor(str(tmp_path), "app")

    assert extractor.extract_strings([_source_file(tmp_path)])
    assert sentinel.read_text(encoding="utf-8") == "USER DATA"
    assert extractor.pot_file.exists()
    assert not list(locale_dir.glob(".langforge-*"))


def test_extract_does_not_follow_legacy_temp_symlink(tmp_path, monkeypatch):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET", encoding="utf-8")
    sentinel = locale_dir / ".tmp_python.pot"
    sentinel.symlink_to(victim)
    monkeypatch.setattr("core.extractor.subprocess.run", _fake_gettext)

    extractor = GettextExtractor(str(tmp_path), "app")

    assert extractor.extract_strings([_source_file(tmp_path)])
    assert sentinel.is_symlink()
    assert victim.read_text(encoding="utf-8") == "SECRET"


def test_invalid_extraction_preserves_existing_pot(tmp_path, monkeypatch):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    existing_pot = locale_dir / "app.pot"
    existing_pot.write_text(_VALID_POT, encoding="utf-8")

    def fake_empty_gettext(cmd, **_kwargs):
        output_arg = next(arg for arg in cmd if arg.startswith("--output="))
        Path(output_arg.removeprefix("--output=")).write_text(
            'msgid ""\nmsgstr ""\n', encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("core.extractor.subprocess.run", fake_empty_gettext)
    extractor = GettextExtractor(str(tmp_path), "app")

    with pytest.raises(RuntimeError, match="No translatable strings"):
        extractor.extract_strings([_source_file(tmp_path)])

    assert existing_pot.read_text(encoding="utf-8") == _VALID_POT


def test_merged_output_does_not_follow_destination_symlink(tmp_path, monkeypatch):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET", encoding="utf-8")
    destination = locale_dir / "app.pot"
    destination.symlink_to(victim)
    monkeypatch.setattr("core.extractor.subprocess.run", _fake_gettext)

    extractor = GettextExtractor(str(tmp_path), "app")
    sources = [_source_file(tmp_path), _source_file(tmp_path, ".js")]

    assert extractor.extract_strings(sources)
    assert not destination.is_symlink()
    assert victim.read_text(encoding="utf-8") == "SECRET"
    assert 'msgid "Hello"' in destination.read_text(encoding="utf-8")


def test_locale_directory_symlink_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "locale").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        GettextExtractor(str(project), "app")

    assert not (outside / "app.pot").exists()


def test_symlinked_catalog_outside_project_is_ignored(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "app.pot"
    victim.write_text(_VALID_POT, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    links = project / "translations"
    links.mkdir()
    (links / "app.pot").symlink_to(victim)

    extractor = GettextExtractor(str(project), "app")

    assert extractor.locale_dir == project / "locale"
    assert extractor.pot_file != victim


def test_vendored_catalog_does_not_select_dependency_directory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    vendor = project / "vendor" / "dependency"
    vendor.mkdir(parents=True)
    (vendor / "de.po").write_text(_VALID_POT, encoding="utf-8")

    extractor = GettextExtractor(str(project), "app")

    assert extractor.locale_dir == project / "locale"
    assert extractor.pot_file == project / "locale" / "app.pot"


def test_populated_po_directory_wins_over_empty_locale_directory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "locale").mkdir()
    po_dir = project / "po"
    po_dir.mkdir()
    (po_dir / "fr.po").write_text(_VALID_POT, encoding="utf-8")

    extractor = GettextExtractor(str(project), "app")

    assert extractor.locale_dir == po_dir


def test_root_locale_catalog_wins_over_matching_test_fixture(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    locale_dir = project / "locale"
    locale_dir.mkdir()
    (locale_dir / "fr.po").write_text(_VALID_POT, encoding="utf-8")
    fixture_dir = project / "tests" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "app.pot").write_text(_VALID_POT, encoding="utf-8")

    extractor = GettextExtractor(str(project), "app")

    assert extractor.locale_dir == locale_dir
    assert extractor.pot_file == locale_dir / "app.pot"


@pytest.mark.parametrize("textdomain", ["../outside", "a/b", "a\\b", "\0bad"])
def test_unsafe_textdomain_falls_back_inside_project(tmp_path, textdomain):
    project = tmp_path / "project"
    project.mkdir()

    extractor = GettextExtractor(str(project), textdomain)

    assert extractor.textdomain == "project"
    assert extractor.pot_file == project / "locale" / "project.pot"
