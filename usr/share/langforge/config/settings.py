"""Gerenciador de configurações do aplicativo."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from api.models import default_model, normalize_model

log = logging.getLogger(__name__)

_SECTION_PROVIDERS = {
    "free_api": (
        "deepl-free",
        "groq",
        "gemini-free",
        "openrouter",
        "mistral-free",
        "libretranslate",
    ),
    "paid_api": ("openai", "gemini", "grok", "deepseek"),
}

# Try to use system keyring for API key storage
_secret_available = False
try:
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    _SECRET_SCHEMA = Secret.Schema.new(
        "org.communitybig.langforge",
        Secret.SchemaFlags.NONE,
        {"key_name": Secret.SchemaAttributeType.STRING},
    )
    _secret_available = True
except Exception:
    pass


def _store_secret(key_name: str, value: str) -> bool:
    """Store a secret in the system keyring, or clear it if value is empty."""
    if not _secret_available:
        return False
    try:
        if value:
            Secret.password_store_sync(
                _SECRET_SCHEMA,
                {"key_name": key_name},
                Secret.COLLECTION_DEFAULT,
                f"LangForge: {key_name}",
                value,
                None,
            )
        else:
            Secret.password_clear_sync(
                _SECRET_SCHEMA,
                {"key_name": key_name},
                None,
            )
        return True
    except Exception as e:
        log.debug("Keyring store failed: %s", e)
        return False


def _lookup_secret(key_name: str) -> str:
    """Retrieve a secret from the system keyring."""
    if not _secret_available:
        return ""
    try:
        value = Secret.password_lookup_sync(
            _SECRET_SCHEMA,
            {"key_name": key_name},
            None,
        )
        return value or ""
    except Exception as e:
        log.debug("Keyring lookup failed: %s", e)
        return ""


def _merge_config(defaults: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge valid loaded values into defaults."""
    merged = json.loads(json.dumps(defaults))
    for key, value in loaded.items():
        if key not in merged:
            merged[key] = value
        elif isinstance(merged[key], dict):
            if isinstance(value, dict):
                merged[key] = _merge_config(merged[key], value)
        elif isinstance(value, type(merged[key])):
            merged[key] = value
    return merged


class Settings:
    """Gerencia as configurações persistentes do aplicativo."""

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "langforge"
        self.config_file = self.config_dir / "config.json"
        self._secret_updates: set[str] = set()
        self._legacy_secret_sections: set[str] = set()
        self._needs_secure_save = False
        self._migrate_old_config()
        self._harden_storage()
        self.config = self._load_config()
        if self._needs_secure_save and self.config_file.exists():
            self.save()

    def _harden_storage(self) -> None:
        """Ensure local fallback storage is private."""
        self.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.config_dir, 0o700)
            if self.config_file.exists() and not self.config_file.is_symlink():
                os.chmod(self.config_file, 0o600)
        except OSError as exc:
            log.warning("Could not restrict configuration permissions: %s", exc)

    def _migrate_old_config(self):
        """Migrate config from old 'translation-automator' directory if needed."""
        old_dir = Path.home() / ".config" / "translation-automator"
        old_file = old_dir / "config.json"
        if old_file.exists() and not self.config_file.exists():
            self.config_dir.mkdir(parents=True, exist_ok=True)
            old_file.rename(self.config_file)
            # Remove old directory if empty
            try:
                old_dir.rmdir()
            except OSError:
                pass

    def _load_config(self) -> Dict[str, Any]:
        """Carrega configurações do arquivo JSON + keyring secrets."""
        loaded: Dict[str, Any] = {}
        if self.config_file.is_symlink():
            log.warning("Ignoring symlinked configuration file")
            self._needs_secure_save = True
        elif self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    candidate = json.load(f)
                if not isinstance(candidate, dict):
                    raise ValueError("configuration root must be an object")
                loaded = candidate
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                log.warning("Ignoring invalid configuration file: %s", exc)

        config = _merge_config(self._get_default_config(), loaded)
        if config.get("api_type") not in ("free", "paid"):
            config["api_type"] = "free"

        for section, providers in _SECTION_PROVIDERS.items():
            section_config = config[section]
            loaded_section = loaded.get(section, {})
            section_was_loaded = section in loaded and isinstance(
                loaded_section, dict
            )
            if not section_was_loaded:
                loaded_section = {}
            modern_secret_storage = (
                not section_was_loaded
                or loaded_section.get("secret_schema") == 2
                or "keys" in loaded_section
                or "keyring_providers" in loaded_section
            )
            section_config["secret_schema"] = 2 if modern_secret_storage else 1

            provider = section_config.get("provider")
            if provider not in providers:
                provider = self._get_default_config()[section]["provider"]
                section_config["provider"] = provider

            loaded_models = loaded_section.get("models", {})
            if not isinstance(loaded_models, dict):
                loaded_models = {}
            legacy_model = loaded_section.get("model", "")
            if (
                isinstance(legacy_model, str)
                and legacy_model
                and provider not in loaded_models
            ):
                section_config["models"][provider] = normalize_model(
                    provider, legacy_model
                )
                self._needs_secure_save = True
            for model_provider in providers:
                section_config["models"][model_provider] = normalize_model(
                    model_provider,
                    section_config["models"].get(model_provider, ""),
                )
            section_config.pop("model", None)

            raw_keys = loaded_section.get("keys", {})
            if not isinstance(raw_keys, dict):
                raw_keys = {}
            keys = {
                key_provider: value
                for key_provider, value in raw_keys.items()
                if isinstance(key_provider, str) and isinstance(value, str)
            }
            if any(keys.values()):
                self._needs_secure_save = True
            keyring_providers = section_config.get("keyring_providers", [])
            if not isinstance(keyring_providers, list):
                keyring_providers = []
            keyring_providers = {
                key_provider
                for key_provider in keyring_providers
                if isinstance(key_provider, str)
            }

            legacy_key = loaded_section.get("api_key", "")
            if isinstance(legacy_key, str) and legacy_key and not keys.get(provider):
                keys[provider] = legacy_key
                section_config["secret_schema"] = 2
                self._legacy_secret_sections.add(section)
                self._needs_secure_save = True
            elif not keys.get(provider) and not modern_secret_storage:
                legacy_secret = _lookup_secret(f"{section}_api_key")
                if legacy_secret:
                    keys[provider] = legacy_secret
                    section_config["secret_schema"] = 2
                    self._legacy_secret_sections.add(section)
                    self._needs_secure_save = True

            # Old releases represented keyring entries as empty key values.
            legacy_keyring_candidates = {
                key_provider
                for key_provider, value in raw_keys.items()
                if isinstance(key_provider, str) and value == ""
            }
            for key_provider in keyring_providers | legacy_keyring_candidates:
                if not keys.get(key_provider):
                    secret = _lookup_secret(f"{section}_{key_provider}_key")
                    if secret:
                        keys[key_provider] = secret
                        keyring_providers.add(key_provider)
                        if key_provider in legacy_keyring_candidates:
                            self._needs_secure_save = True

            section_config["api_key"] = ""
            section_config["keys"] = keys
            section_config["keyring_providers"] = sorted(keyring_providers)

        return config

    def _get_default_config(self) -> Dict[str, Any]:
        """Retorna configuração padrão."""
        return {
            "api_type": "free",
            "free_api": {
                "provider": "groq",
                "api_key": "",
                "secret_schema": 2,
                "keys": {},  # Per-provider keys: {"groq": "key1", "deepl-free": "key2"}
                "keyring_providers": [],
                "libretranslate_url": "https://libretranslate.com",
                "models": {
                    provider: default_model(provider)
                    for provider in _SECTION_PROVIDERS["free_api"]
                },
            },
            "paid_api": {
                "provider": "openai",  # openai, gemini, grok
                "api_key": "",
                "secret_schema": 2,
                "keys": {},  # Per-provider keys: {"openai": "sk-...", "gemini": "AI..."}
                "keyring_providers": [],
                "models": {
                    provider: default_model(provider)
                    for provider in _SECTION_PROVIDERS["paid_api"]
                },
            },
            "fix_context": {
                "reference_lang": "fr",  # pt-BR, fr, or es
            },
        }

    def save(self):
        """Salva configurações no arquivo JSON.

        Secrets use the system keyring when possible. The private JSON file is
        only a fallback when keyring storage fails.
        """
        self._harden_storage()
        config_to_save = json.loads(json.dumps(self.config))
        plaintext_fallback = False

        for section, providers in _SECTION_PROVIDERS.items():
            live_section = self.config.setdefault(section, {})
            live_keys = live_section.setdefault("keys", {})
            active_provider = live_section.get("provider", providers[0])
            legacy_key = live_section.get("api_key", "")
            if legacy_key and not live_keys.get(active_provider):
                live_keys[active_provider] = legacy_key
                live_section["api_key"] = ""
            stored = set(live_section.get("keyring_providers", []))
            saved_section = config_to_save.setdefault(section, {})
            saved_keys = saved_section.setdefault("keys", {})

            for provider in set(providers) | set(live_keys):
                key = live_keys.get(provider, "")
                secret_name = f"{section}_{provider}_key"
                if key:
                    if _store_secret(secret_name, key):
                        saved_keys.pop(provider, None)
                        stored.add(provider)
                    else:
                        saved_keys[provider] = key
                        stored.discard(provider)
                        plaintext_fallback = True
                else:
                    saved_keys.pop(provider, None)
                    if secret_name in self._secret_updates:
                        stored.discard(provider)
                        _store_secret(secret_name, "")

            live_section["keyring_providers"] = sorted(stored)
            saved_section["keyring_providers"] = sorted(stored)
            saved_section["api_key"] = ""
            saved_section["secret_schema"] = live_section.get("secret_schema", 2)
            saved_section.pop("model", None)

            if section in self._legacy_secret_sections:
                _store_secret(f"{section}_api_key", "")

        if plaintext_fallback:
            log.warning(
                "System keyring unavailable; API keys remain in the private "
                "configuration file"
            )

        fd, temporary_name = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=self.config_dir
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as temporary:
                json.dump(config_to_save, temporary, indent=2, ensure_ascii=False)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.config_file)
            os.chmod(self.config_file, 0o600)
            directory_fd = os.open(self.config_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._secret_updates.clear()
            self._legacy_secret_sections.clear()
            self._needs_secure_save = False
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    def get(self, key: str, default=None) -> Any:
        """Obtém valor de configuração."""
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, default)
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        """Define valor de configuração."""
        keys = key.split(".")
        config = self.config
        for k in keys[:-1]:
            if not isinstance(config.get(k), dict):
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value

    def get_api_type(self) -> str:
        """Retorna tipo de API configurada (free/paid)."""
        return self.config.get("api_type", "free")

    def set_api_type(self, api_type: str):
        """Define tipo de API (free/paid)."""
        self.config["api_type"] = api_type

    def get_free_provider(self) -> str:
        """Retorna provider de API gratuita."""
        return self.config.get("free_api", {}).get("provider", "libretranslate")

    def is_first_run(self) -> bool:
        """Check if this is the first run (no config file existed)."""
        return not self.config_file.exists()

    def get_paid_provider(self) -> str:
        """Retorna provider de API paga."""
        return self.config.get("paid_api", {}).get("provider", "openai")

    def get_provider_key(self, section: str, provider: str) -> str:
        """Get API key for a specific provider.

        Checks per-provider keys first, then falls back to the legacy
        shared api_key field only when no per-provider keys exist at all
        (old config format).

        Args:
            section: 'free_api' or 'paid_api'
            provider: Provider ID (e.g. 'openai', 'groq')
        """
        # Per-provider key (new format)
        section_config = self.config.get(section, {})
        keys = section_config.get("keys", {})
        key = keys.get(provider, "")
        if not key and provider in section_config.get("keyring_providers", []):
            # Try keyring
            key = _lookup_secret(f"{section}_{provider}_key")
        if key:
            return key
        # Fallback to legacy shared key only if no per-provider keys exist
        # (indicates old config format before per-provider storage)
        if not any(keys.values()):
            return self.config.get(section, {}).get("api_key", "")
        return ""

    def set_provider_key(self, section: str, provider: str, key: str) -> None:
        """Set API key for a specific provider."""
        section_config = self.config.setdefault(section, {})
        section_config.setdefault("keys", {})[provider] = key
        section_config["secret_schema"] = 2
        self._secret_updates.add(f"{section}_{provider}_key")

    def get_provider_model(self, section: str, provider: str) -> str:
        """Return the selected model for a provider."""
        model = self.config.get(section, {}).get("models", {}).get(provider, "")
        normalized = normalize_model(provider, model)
        self.config.setdefault(section, {}).setdefault("models", {})[provider] = (
            normalized
        )
        return normalized

    def set_provider_model(self, section: str, provider: str, model: str) -> None:
        """Store a model selection without affecting other providers."""
        self.config.setdefault(section, {}).setdefault("models", {})[provider] = (
            normalize_model(provider, model)
        )

    def get_reference_lang(self) -> str:
        """Return the fix_context reference language ('auto' or a code like 'pt-BR')."""
        return self.config.get("fix_context", {}).get("reference_lang", "fr")

    def set_reference_lang(self, lang: str) -> None:
        """Set the fix_context reference language."""
        self.config.setdefault("fix_context", {})["reference_lang"] = lang
