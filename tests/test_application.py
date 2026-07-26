"""Focused tests for desktop file opening."""

from types import SimpleNamespace

import pytest
from gi.repository import Gio

import main


class WindowHarness:
    """Exercise the real drop routing without constructing a GTK window."""

    def __init__(self, *, is_translating=False):
        self.events = []
        self.controller = SimpleNamespace(is_translating=is_translating)

    def present(self):
        self.events.append(("present", None))

    def _on_drop(self, target, value, x, y):
        return main.MainWindow._on_drop(self, target, value, x, y)

    def _validate_and_set_project(self, path):
        self.events.append(("project", path))

    def _validate_and_set_file(self, path):
        self.events.append(("file", path))


def _open(window, files):
    app = SimpleNamespace(_get_main_window=lambda: window)
    main.LangForgeApp.do_open(app, files, len(files), "")


def test_application_handles_open():
    app = main.LangForgeApp()

    assert app.get_flags() & Gio.ApplicationFlags.HANDLES_OPEN


@pytest.mark.parametrize(
    ("target_kind", "expected_event"),
    [("file", "file"), ("directory", "project")],
)
def test_do_open_routes_local_target(tmp_path, target_kind, expected_event):
    target = tmp_path / "target.po"
    if target_kind == "file":
        target.touch()
    else:
        target.mkdir()
    window = WindowHarness()

    _open(window, [Gio.File.new_for_path(str(target))])

    assert window.events == [
        ("present", None),
        (expected_event, str(target)),
    ]


def test_do_open_skips_unusable_targets_and_stops_after_first_valid(tmp_path):
    first = tmp_path / "first.po"
    first.touch()
    second = tmp_path / "second"
    second.mkdir()
    missing = tmp_path / "missing.po"
    window = WindowHarness()

    _open(
        window,
        [
            Gio.File.new_for_uri("https://example.invalid/remote.po"),
            Gio.File.new_for_path(str(missing)),
            Gio.File.new_for_path(str(first)),
            Gio.File.new_for_path(str(second)),
        ],
    )

    assert window.events == [
        ("present", None),
        ("file", str(first)),
    ]


def test_do_open_without_targets_only_presents_window():
    window = WindowHarness()

    _open(window, [])

    assert window.events == [("present", None)]


def test_do_open_ignores_targets_during_translation(tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    window = WindowHarness(is_translating=True)

    _open(window, [Gio.File.new_for_path(str(target))])

    assert window.events == [("present", None)]


def test_do_activate_preserves_normal_window_activation():
    window = WindowHarness()
    app = SimpleNamespace(_get_main_window=lambda: window)

    main.LangForgeApp.do_activate(app)

    assert window.events == [("present", None)]


def test_get_main_window_reuses_inactive_main_window(monkeypatch):
    class FakeMainWindow:
        def __init__(self, app):
            self.app = app

    existing = FakeMainWindow(None)
    app = SimpleNamespace(
        props=SimpleNamespace(active_window=object()),
        get_windows=lambda: [object(), existing],
    )
    monkeypatch.setattr(main, "MainWindow", FakeMainWindow)

    assert main.LangForgeApp._get_main_window(app) is existing


def test_quit_action_delegates_to_existing_main_window(monkeypatch):
    events = []

    class FakeMainWindow:
        def request_quit(self):
            events.append("request-quit")

    window = FakeMainWindow()
    app = SimpleNamespace(
        props=SimpleNamespace(active_window=object()),
        get_windows=lambda: [window],
        quit=lambda: events.append("quit"),
    )
    monkeypatch.setattr(main, "MainWindow", FakeMainWindow)

    main.LangForgeApp._on_quit(app, None, None)

    assert events == ["request-quit"]


def test_quit_action_without_main_window_quits_immediately(monkeypatch):
    events = []

    class FakeMainWindow:
        pass

    app = SimpleNamespace(
        props=SimpleNamespace(active_window=object()),
        get_windows=lambda: [object()],
        quit=lambda: events.append("quit"),
    )
    monkeypatch.setattr(main, "MainWindow", FakeMainWindow)

    main.LangForgeApp._on_quit(app, None, None)

    assert events == ["quit"]
