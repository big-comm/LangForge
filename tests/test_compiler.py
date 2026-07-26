"""Tests for atomic gettext compilation."""

from pathlib import Path

import polib
import pytest

from core.compiler import MoCompiler


def _write_valid_catalog(locale_dir: Path) -> None:
    template = polib.POFile()
    template.append(polib.POEntry(msgid="Open"))
    template.save(str(locale_dir / "app.pot"))

    catalog = polib.POFile()
    catalog.metadata = {
        "Language": "fr",
        "Content-Type": "text/plain; charset=UTF-8",
    }
    catalog.append(polib.POEntry(msgid="Open", msgstr="Ouvrir"))
    catalog.save(str(locale_dir / "fr.po"))


def test_compile_language_writes_valid_mo_atomically(tmp_path):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    _write_valid_catalog(locale_dir)

    MoCompiler(tmp_path, "app").compile_language("fr")

    output = tmp_path / "usr/share/locale/fr/LC_MESSAGES/app.mo"
    assert output.read_bytes().startswith(b"\xde\x12\x04\x95")
    assert not list(output.parent.glob(".app.*.mo.tmp"))


def test_failed_compile_preserves_existing_mo(tmp_path):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    (locale_dir / "app.pot").write_text("", encoding="utf-8")
    (locale_dir / "fr.po").write_text(
        'msgid "duplicate"\nmsgstr "one"\n\nmsgid "duplicate"\nmsgstr "two"\n',
        encoding="utf-8",
    )
    output = tmp_path / "usr/share/locale/fr/LC_MESSAGES/app.mo"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"existing-mo")

    with pytest.raises(RuntimeError, match="Could not compile"):
        MoCompiler(tmp_path, "app").compile_language("fr")

    assert output.read_bytes() == b"existing-mo"
    assert not list(output.parent.glob(".app.*.mo.tmp"))


def test_compile_replaces_output_symlink_without_following_it(tmp_path):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    _write_valid_catalog(locale_dir)
    victim = tmp_path / "victim"
    victim.write_bytes(b"do-not-overwrite")
    output = tmp_path / "usr/share/locale/fr/LC_MESSAGES/app.mo"
    output.parent.mkdir(parents=True)
    output.symlink_to(victim)

    MoCompiler(tmp_path, "app").compile_language("fr")

    assert victim.read_bytes() == b"do-not-overwrite"
    assert not output.is_symlink()
    assert output.read_bytes().startswith(b"\xde\x12\x04\x95")


def test_compile_rejects_symlinked_output_directory(tmp_path):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    _write_valid_catalog(locale_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    packaged = tmp_path / "usr/share"
    packaged.mkdir(parents=True)
    (packaged / "locale").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="contains a symlink"):
        MoCompiler(tmp_path, "app").compile_language("fr")

    assert list(outside.iterdir()) == []


def test_vendored_catalog_does_not_select_dependency_directory(tmp_path):
    vendor = tmp_path / "vendor" / "dependency"
    vendor.mkdir(parents=True)
    (vendor / "de.po").write_text('msgid "Vendor"\nmsgstr "Lieferant"\n')

    compiler = MoCompiler(tmp_path, "app")

    assert compiler.locale_dir == tmp_path / "locale"


def test_compile_rejects_normalized_locale_collision(tmp_path):
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    (locale_dir / "app.pot").write_text("", encoding="utf-8")
    for name in ("pt-BR.po", "pt_BR.po"):
        catalog = polib.POFile()
        catalog.append(polib.POEntry(msgid="Open", msgstr="Abrir"))
        catalog.save(str(locale_dir / name))

    with pytest.raises(RuntimeError, match="collide after locale normalization"):
        MoCompiler(tmp_path, "app").compile_all()

    assert not (tmp_path / "usr/share/locale/pt_BR").exists()
