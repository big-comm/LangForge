"""Tests for config.settings — load, save, get, set."""

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "usr" / "share" / "langforge"))

import config.settings as settings_module
from api.models import default_model
from config.settings import Settings


class TestSettings:
    def test_default_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        assert s.get_api_type() == "free"
        assert s.get("free_api.provider") == "groq"
        assert s.get("paid_api.provider") == "openai"
        assert s.get_provider_model("free_api", "groq") == default_model("groq")
        assert s.get_provider_model("paid_api", "openai") == default_model("openai")

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
        assert s.get("free_api.models.groq") == default_model("groq")

    def test_get_default_for_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        assert s.get("nonexistent.key", "fallback") == "fallback"

    def test_set_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        s = Settings()
        s.set_provider_model("paid_api", "openai", "gpt-5.6-terra")
        s.set_provider_model("paid_api", "deepseek", "deepseek-v4-pro")
        assert s.get_provider_model("paid_api", "openai") == "gpt-5.6-terra"
        assert s.get_provider_model("paid_api", "deepseek") == "deepseek-v4-pro"

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

    def test_keyring_storage_scrubs_json_and_uses_private_mode(
        self, tmp_path, monkeypatch
    ):
        secrets = {}
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            settings_module,
            "_store_secret",
            lambda name, value: secrets.__setitem__(name, value) is None,
        )
        monkeypatch.setattr(
            settings_module, "_lookup_secret", lambda name: secrets.get(name, "")
        )

        settings = Settings()
        settings.set_provider_key("paid_api", "openai", "sk-private")
        settings.save()

        raw = settings.config_file.read_text(encoding="utf-8")
        persisted = json.loads(raw)
        assert "sk-private" not in raw
        assert persisted["paid_api"]["keyring_providers"] == ["openai"]
        assert secrets["paid_api_openai_key"] == "sk-private"
        assert stat.S_IMODE(settings.config_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(settings.config_dir.stat().st_mode) == 0o700

        reloaded = Settings()
        assert reloaded.get_provider_key("paid_api", "openai") == "sk-private"

    def test_private_plaintext_fallback_when_keyring_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(settings_module, "_store_secret", lambda *_: False)
        monkeypatch.setattr(settings_module, "_lookup_secret", lambda *_: "")

        settings = Settings()
        settings.set_provider_key("free_api", "groq", "gsk-fallback")
        settings.save()

        raw = settings.config_file.read_text(encoding="utf-8")
        assert "gsk-fallback" in raw
        assert stat.S_IMODE(settings.config_file.stat().st_mode) == 0o600

    def test_cleared_key_is_not_resurrected_after_keyring_failure(
        self, tmp_path, monkeypatch
    ):
        secrets = {}
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        def store(name, value):
            if value:
                secrets[name] = value
                return True
            return False

        monkeypatch.setattr(settings_module, "_store_secret", store)
        monkeypatch.setattr(
            settings_module, "_lookup_secret", lambda name: secrets.get(name, "")
        )

        settings = Settings()
        settings.set_provider_key("paid_api", "openai", "sk-delete-me")
        settings.save()
        settings.set_provider_key("paid_api", "openai", "")
        settings.save()

        reloaded = Settings()
        assert reloaded.get_provider_key("paid_api", "openai") == ""
        persisted = json.loads(reloaded.config_file.read_text(encoding="utf-8"))
        assert persisted["paid_api"]["keyring_providers"] == []

    def test_temporary_keyring_lookup_failure_preserves_secret_marker(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".config" / "langforge"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "paid_api": {
                        "provider": "openai",
                        "keyring_providers": ["openai"],
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings_module, "_lookup_secret", lambda *_: "")
        monkeypatch.setattr(settings_module, "_store_secret", lambda *_: False)

        settings = Settings()
        settings.set("fix_context.reference_lang", "es")
        settings.save()

        persisted = json.loads(config_file.read_text(encoding="utf-8"))
        assert persisted["paid_api"]["keyring_providers"] == ["openai"]

    def test_cleared_migrated_key_is_not_restored_from_legacy_secret(
        self, tmp_path, monkeypatch
    ):
        secrets = {"free_api_api_key": "legacy-key"}
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".config" / "langforge"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(
            json.dumps({"free_api": {"provider": "groq"}}),
            encoding="utf-8",
        )

        def store(name, value):
            if not value and name == "free_api_api_key":
                return False
            if value:
                secrets[name] = value
            else:
                secrets.pop(name, None)
            return True

        monkeypatch.setattr(settings_module, "_store_secret", store)
        monkeypatch.setattr(
            settings_module,
            "_lookup_secret",
            lambda name: secrets.get(name, ""),
        )

        settings = Settings()
        assert settings.get_provider_key("free_api", "groq") == "legacy-key"
        settings.set_provider_key("free_api", "groq", "")
        settings.save()

        reloaded = Settings()
        assert secrets["free_api_api_key"] == "legacy-key"
        assert reloaded.get_provider_key("free_api", "groq") == ""
        persisted = json.loads(config_file.read_text(encoding="utf-8"))
        assert persisted["free_api"]["secret_schema"] == 2

    def test_invalid_json_root_falls_back_to_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".config" / "langforge"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text("[]", encoding="utf-8")

        settings = Settings()

        assert settings.get_api_type() == "free"
        assert settings.get_free_provider() == "groq"

    def test_symlinked_config_is_replaced_without_following_it(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(settings_module, "_store_secret", lambda *_: False)
        config_dir = tmp_path / ".config" / "langforge"
        config_dir.mkdir(parents=True)
        victim = tmp_path / "victim.json"
        victim.write_text('{"do_not_touch": true}', encoding="utf-8")
        config_file = config_dir / "config.json"
        config_file.symlink_to(victim)

        settings = Settings()

        assert victim.read_text(encoding="utf-8") == '{"do_not_touch": true}'
        assert not config_file.is_symlink()
        assert settings.get_api_type() == "free"
        assert stat.S_IMODE(config_file.stat().st_mode) == 0o600

    def test_migrates_legacy_model_and_plaintext_key(self, tmp_path, monkeypatch):
        secrets = {}
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            settings_module,
            "_store_secret",
            lambda name, value: secrets.__setitem__(name, value) is None,
        )
        monkeypatch.setattr(
            settings_module, "_lookup_secret", lambda name: secrets.get(name, "")
        )
        config_dir = tmp_path / ".config" / "langforge"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "api_type": "paid",
                    "paid_api": {
                        "provider": "deepseek",
                        "api_key": "legacy-secret",
                        "model": "deepseek-chat",
                    },
                }
            ),
            encoding="utf-8",
        )

        settings = Settings()

        assert (
            settings.get_provider_model("paid_api", "deepseek") == "deepseek-v4-flash"
        )
        assert settings.get_provider_key("paid_api", "deepseek") == "legacy-secret"
        raw = config_file.read_text(encoding="utf-8")
        assert "legacy-secret" not in raw
        assert "deepseek-chat" not in raw
        assert secrets["paid_api_deepseek_key"] == "legacy-secret"
