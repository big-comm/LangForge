"""Tests for config.settings — load, save, get, set."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "usr" / "share" / "langforge"))

from config.settings import Settings


class TestSettings:
    def test_default_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        assert s.get_api_type() == "free"
        assert s.get("free_api.provider") == "libretranslate"
        assert s.get("paid_api.provider") == "openai"

    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        s.set("free_api.provider", "deepl-free")
        s.set("free_api.api_key", "test-key-123")
        s.save()

        # Reload
        s2 = Settings()
        assert s2.get("free_api.provider") == "deepl-free"

    def test_get_nested_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        assert s.get("free_api.model") == ""

    def test_get_default_for_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        assert s.get("nonexistent.key", "fallback") == "fallback"

    def test_set_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        s.set("paid_api.model", "gpt-4o")
        assert s.get("paid_api.model") == "gpt-4o"

    def test_is_first_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        assert s.is_first_run() is True
        s.save()
        s2 = Settings()
        assert s2.is_first_run() is False

    def test_migrate_old_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        old_dir = tmp_path / ".config" / "translation-automator"
        old_dir.mkdir(parents=True)
        config_data = {"api_type": "paid", "paid_api": {"provider": "grok"}}
        (old_dir / "config.json").write_text(json.dumps(config_data))

        s = Settings()
        assert s.get_api_type() == "paid"
        assert s.get("paid_api.provider") == "grok"
        # Old file should be moved
        assert not (old_dir / "config.json").exists()

    def test_set_api_type(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        s.set_api_type("paid")
        assert s.get_api_type() == "paid"
