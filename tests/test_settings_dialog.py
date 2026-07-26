"""Focused tests for provider switching in the settings dialog."""

import threading
from types import SimpleNamespace

from ui import settings_dialog
from ui.settings_dialog import SettingsDialog, _model_display_name


class FakeEntry:
    def __init__(self, text):
        self.text = text

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text


class FakeSettings:
    def __init__(self, keys):
        self.keys = dict(keys)
        self.values = {}
        self.set_calls = []

    def get_provider_key(self, section, provider):
        return self.keys.get((section, provider), "")

    def set_provider_key(self, section, provider, key):
        self.set_calls.append((section, provider, key))
        self.keys[(section, provider)] = key

    def set(self, key, value):
        self.values[key] = value


def test_model_labels_expose_recommendation_and_paid_price():
    paid = _model_display_name("openai", "gpt-5.6-luna")
    free = _model_display_name("groq", "openai/gpt-oss-120b")

    assert paid == "★ GPT-5.6 Luna · $1/$6 / 1M"
    assert free == "★ GPT-OSS 120B"


def test_free_provider_switches_keep_each_key_isolated():
    settings = FakeSettings(
        {
            ("free_api", "gemini-free"): "gemini-saved",
            ("free_api", "openrouter"): "openrouter-saved",
        }
    )
    saved_models = []
    dialog = SimpleNamespace(
        settings=settings,
        _current_free_provider="groq",
        _loading_key_fields=False,
        _free_key_dirty=True,
        free_api_key=FakeEntry("groq-edited"),
        _save_selected_free_model=saved_models.append,
        _update_free_api_fields=lambda: None,
    )
    active = SimpleNamespace(get_active=lambda: True)

    SettingsDialog._on_free_provider_toggled(dialog, active, "gemini-free")
    assert settings.keys[("free_api", "groq")] == "groq-edited"
    assert dialog.free_api_key.get_text() == "gemini-saved"

    dialog.free_api_key.set_text("gemini-edited")
    dialog._free_key_dirty = True
    SettingsDialog._on_free_provider_toggled(dialog, active, "openrouter")

    assert settings.keys[("free_api", "gemini-free")] == "gemini-edited"
    assert settings.keys[("free_api", "groq")] == "groq-edited"
    assert dialog.free_api_key.get_text() == "openrouter-saved"
    assert dialog._current_free_provider == "openrouter"
    assert settings.values["free_api.provider"] == "openrouter"
    assert saved_models == ["groq", "gemini-free"]


def test_paid_provider_switches_keep_each_key_isolated():
    settings = FakeSettings(
        {
            ("paid_api", "deepseek"): "deepseek-saved",
            ("paid_api", "grok"): "grok-saved",
        }
    )
    saved_models = []
    dialog = SimpleNamespace(
        settings=settings,
        _current_paid_provider="openai",
        _loading_key_fields=False,
        _paid_key_dirty=True,
        api_key=FakeEntry("openai-edited"),
        _save_selected_paid_model=saved_models.append,
        _update_paid_provider_subtitle=lambda: None,
        _update_paid_model_list=lambda: None,
    )
    active = SimpleNamespace(get_active=lambda: True)

    SettingsDialog._on_paid_provider_toggled(dialog, active, "deepseek")
    assert settings.keys[("paid_api", "openai")] == "openai-edited"
    assert dialog.api_key.get_text() == "deepseek-saved"

    dialog.api_key.set_text("deepseek-edited")
    dialog._paid_key_dirty = True
    SettingsDialog._on_paid_provider_toggled(dialog, active, "grok")

    assert settings.keys[("paid_api", "deepseek")] == "deepseek-edited"
    assert settings.keys[("paid_api", "openai")] == "openai-edited"
    assert dialog.api_key.get_text() == "grok-saved"
    assert dialog._current_paid_provider == "grok"
    assert settings.values["paid_api.provider"] == "grok"
    assert saved_models == ["openai", "deepseek"]


def test_provider_switch_does_not_clear_an_unavailable_untouched_key():
    settings = FakeSettings(
        {("free_api", "gemini-free"): "gemini-saved"}
    )
    dialog = SimpleNamespace(
        settings=settings,
        _current_free_provider="groq",
        _loading_key_fields=False,
        _free_key_dirty=False,
        free_api_key=FakeEntry(""),
        _save_selected_free_model=lambda _provider: None,
        _update_free_api_fields=lambda: None,
    )
    active = SimpleNamespace(get_active=lambda: True)

    SettingsDialog._on_free_provider_toggled(dialog, active, "gemini-free")

    assert settings.set_calls == []
    assert dialog.free_api_key.get_text() == "gemini-saved"


def test_connection_worker_uses_main_thread_widget_snapshot(monkeypatch):
    targets = []
    captured = {}

    class DeferredThread:
        def __init__(self, target, daemon):
            assert daemon is True
            targets.append(target)

        def start(self):
            pass

    class FakeAPI:
        def test_connection(self):
            return True

    def create(provider, api_key="", **kwargs):
        captured.update(
            provider=provider,
            api_key=api_key,
            model=kwargs.get("model"),
        )
        return FakeAPI()

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    monkeypatch.setattr(settings_dialog.APIFactory, "create", staticmethod(create))
    monkeypatch.setattr(
        settings_dialog,
        "GLib",
        SimpleNamespace(idle_add=lambda callback, *args: callback(*args)),
    )

    entry = FakeEntry("key-from-main-thread")
    button = SimpleNamespace(
        set_sensitive=lambda _value: None,
        set_label=lambda _value: None,
    )
    dialog = SimpleNamespace(
        api_type_row=SimpleNamespace(get_selected=lambda: 0),
        _get_selected_free_provider=lambda: "groq",
        _get_selected_paid_provider=lambda: "openai",
        free_api_key=entry,
        api_key=FakeEntry("paid-key"),
        libretranslate_url=FakeEntry("https://example.invalid"),
        free_model_row=SimpleNamespace(get_selected=lambda: 0),
        _paid_model_checks={},
        _set_connection_status=lambda *_args: None,
        _reset_test_button=lambda _button: None,
    )

    SettingsDialog._on_test_connection(dialog, button)
    entry.set_text("mutated-after-thread-start")
    targets[0]()

    assert captured == {
        "provider": "groq",
        "api_key": "key-from-main-thread",
        "model": "openai/gpt-oss-120b",
    }
