"""Focused tests for main-window settings synchronization."""

from types import SimpleNamespace

import pytest

from ui import main_window


class FakeWidget:
    def __init__(self, *, sensitive=False):
        self.sensitive = sensitive
        self.label = ""
        self.progress = None

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def set_label(self, label):
        self.label = label

    def set_progress(self, progress):
        self.progress = progress


class LifecycleHarness:
    request_quit = main_window.MainWindow.request_quit
    _on_close_request = main_window.MainWindow._on_close_request
    _defer_quit_for_translation = main_window.MainWindow._defer_quit_for_translation
    _on_cancel_translation = main_window.MainWindow._on_cancel_translation
    _finish_translation = main_window.MainWindow._finish_translation
    _quit_when_translation_stops = main_window.MainWindow._quit_when_translation_stops

    def __init__(self, *, is_translating):
        self.cancel_calls = 0
        self.quit_calls = 0
        self.controller = SimpleNamespace(
            is_translating=is_translating,
            cancel=self._cancel,
            _api_client=None,
        )
        self._quit_requested = False
        self._quit_check_scheduled = False
        self.translate_button = FakeWidget(sensitive=False)
        self.cancel_button = FakeWidget(sensitive=True)
        self.progress_title = FakeWidget()
        self.progress_subtitle = FakeWidget()
        self.progress_ring = FakeWidget()
        self.lang_widgets = {}
        self._app = SimpleNamespace(quit=self._quit)

    def _cancel(self):
        self.cancel_calls += 1

    def _quit(self):
        self.quit_calls += 1

    def get_application(self):
        return self._app


@pytest.mark.parametrize(
    ("handler_name", "expected_events"),
    [
        ("_on_settings_closed", ["refresh"]),
        ("_on_welcome_settings_closed", ["refresh", "drop"]),
    ],
)
def test_settings_reload_updates_controller(monkeypatch, handler_name, expected_events):
    reloaded_settings = object()
    monkeypatch.setattr(main_window, "Settings", lambda: reloaded_settings)

    events = []
    window = SimpleNamespace(
        settings=object(),
        controller=SimpleNamespace(settings=object()),
        _refresh_api_dropdowns=lambda: events.append("refresh"),
        stack=SimpleNamespace(set_visible_child_name=lambda name: events.append(name)),
    )

    result = getattr(main_window.MainWindow, handler_name)(window, None)

    assert result is False
    assert window.settings is reloaded_settings
    assert window.controller.settings is reloaded_settings
    assert events == expected_events


def test_sidebar_fallback_updates_effective_provider(monkeypatch):
    values = {
        "api_type": "free",
        "free_api.provider": "groq",
    }

    class FakeSettings:
        def get(self, key, default=None):
            return values.get(key, default)

        def set(self, key, value):
            values[key] = value

    class FakeRow:
        def __init__(self):
            self.selected = 0
            self.model = None

        def get_selected(self):
            return self.selected

        def set_selected(self, selected):
            self.selected = selected

        def set_model(self, model):
            self.model = model

        def set_sensitive(self, _sensitive):
            pass

    monkeypatch.setattr(
        main_window,
        "Gtk",
        SimpleNamespace(StringList=SimpleNamespace(new=lambda values: list(values))),
    )
    window = SimpleNamespace(
        settings=FakeSettings(),
        api_type_row=FakeRow(),
        api_provider_row=FakeRow(),
        _configured_types=[("Free", "free")],
        _configured_free_providers=[("LibreTranslate", "libretranslate")],
        _configured_paid_providers=[],
    )

    main_window.MainWindow._update_sidebar_providers(window)

    assert values["free_api.provider"] == "libretranslate"
    assert window.api_provider_row.model == ["LibreTranslate"]


def test_result_page_does_not_report_partial_failure_as_success():
    class FakePage:
        def __init__(self):
            self.icon = ""
            self.title = ""
            self.description = ""

        def set_icon_name(self, icon):
            self.icon = icon

        def set_title(self, title):
            self.title = title

        def set_description(self, description):
            self.description = description

    page = FakePage()
    window = SimpleNamespace(
        success_page=page,
        controller=SimpleNamespace(_last_usage={}),
        stack=SimpleNamespace(set_visible_child_name=lambda _name: None),
    )

    main_window.MainWindow._show_success_page(window, 2, 5, 1)

    assert page.icon == "dialog-warning-symbolic"
    assert page.title == main_window._("Error")
    assert "1 " + main_window._("error") in page.description

    main_window.MainWindow._show_success_page(window, 3, 5, 0)
    assert page.icon == "emblem-ok-symbolic"
    assert page.title == main_window._("Translation Complete!")


def test_close_during_translation_waits_for_callback_and_worker(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        main_window.GLib,
        "timeout_add",
        lambda interval, callback: scheduled.append((interval, callback)) or 1,
    )
    window = LifecycleHarness(is_translating=True)

    assert window._on_close_request() is True
    assert window._on_close_request() is True
    assert window.cancel_calls == 1
    assert window.quit_calls == 0
    assert window.translate_button.sensitive is False
    assert scheduled == []

    window._finish_translation()

    assert window.translate_button.sensitive is True
    assert len(scheduled) == 1
    assert scheduled[0][0] == 25
    assert scheduled[0][1]() is True
    assert window.quit_calls == 0

    window.controller.is_translating = False
    assert scheduled[0][1]() is False
    assert window.quit_calls == 1
    assert window._quit_requested is False


def test_request_quit_without_translation_exits_immediately():
    window = LifecycleHarness(is_translating=False)

    window.request_quit()

    assert window.cancel_calls == 0
    assert window.quit_calls == 1


@pytest.mark.parametrize(
    ("handler_name", "args", "expected"),
    [
        ("_on_select_project", (None,), None),
        ("_on_select_file", (None,), None),
        ("_on_folder_selected", (object(), object()), None),
        ("_on_file_selected", (object(), object()), None),
        ("_on_drop", (None, object(), 0, 0), False),
        ("_validate_and_set_project", ("/tmp/project",), None),
        ("_validate_and_set_file", ("/tmp/file.po",), None),
    ],
)
def test_open_and_switch_handlers_are_ignored_during_translation(
    handler_name,
    args,
    expected,
):
    window = SimpleNamespace(controller=SimpleNamespace(is_translating=True))

    result = getattr(main_window.MainWindow, handler_name)(window, *args)

    assert result is expected
