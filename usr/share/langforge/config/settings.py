"""Gerenciador de configurações do aplicativo."""

import json
import logging
from pathlib import Path
from typing import Dict, Any

log = logging.getLogger(__name__)

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
    """Store a secret in the system keyring."""
    if not _secret_available or not value:
        return False
    try:
        Secret.password_store_sync(
            _SECRET_SCHEMA,
            {"key_name": key_name},
            Secret.COLLECTION_DEFAULT,
            f"LangForge: {key_name}",
            value,
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


class Settings:
    """Gerencia as configurações persistentes do aplicativo."""

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "langforge"
        self.config_file = self.config_dir / "config.json"
        self._migrate_old_config()
        self.config = self._load_config()

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
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                config = self._get_default_config()
        else:
            config = self._get_default_config()
        # Recover API keys from keyring if not in file
        for section in ("free_api", "paid_api"):
            if not config.get(section, {}).get("api_key"):
                secret = _lookup_secret(f"{section}_api_key")
                if secret:
                    config.setdefault(section, {})["api_key"] = secret

            # Recover per-provider keys from keyring
            keys = config.get(section, {}).get("keys", {})
            for provider in list(keys.keys()):
                if not keys[provider]:
                    secret = _lookup_secret(f"{section}_{provider}_key")
                    if secret:
                        keys[provider] = secret
            config.setdefault(section, {})["keys"] = keys

        return config

    def _get_default_config(self) -> Dict[str, Any]:
        """Retorna configuração padrão."""
        return {
            "api_type": "free",
            "free_api": {
                "provider": "groq",  # Best free option: 14.4k req/day, excellent quality
                "api_key": "",
                "keys": {},  # Per-provider keys: {"groq": "key1", "deepl-free": "key2"}
                "libretranslate_url": "https://libretranslate.com",
                "model": "",
            },
            "paid_api": {
                "provider": "openai",  # openai, gemini, grok
                "api_key": "",
                "keys": {},  # Per-provider keys: {"openai": "sk-...", "gemini": "AI..."}
                "model": "gpt-4o-mini",
            },
            "fix_context": {
                "reference_lang": "fr",  # pt-BR, fr, or es
            },
        }

    def save(self):
        """Salva configurações no arquivo JSON. API keys go to system keyring."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config_to_save = json.loads(json.dumps(self.config))

        # Move legacy api_key to keyring
        for section in ("free_api", "paid_api"):
            api_key = config_to_save.get(section, {}).get("api_key", "")
            if api_key and _store_secret(f"{section}_api_key", api_key):
                config_to_save[section]["api_key"] = ""

            # Move per-provider keys to keyring
            keys = config_to_save.get(section, {}).get("keys", {})
            for provider, key in keys.items():
                if key and _store_secret(f"{section}_{provider}_key", key):
                    keys[provider] = ""

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config_to_save, f, indent=2, ensure_ascii=False)

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
            if k not in config:
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
        shared api_key field for backward compatibility.

        Args:
            section: 'free_api' or 'paid_api'
            provider: Provider ID (e.g. 'openai', 'groq')
        """
        # Per-provider key (new format)
        keys = self.config.get(section, {}).get("keys", {})
        key = keys.get(provider, "")
        if not key:
            # Try keyring
            key = _lookup_secret(f"{section}_{provider}_key")
        if key:
            return key
        # Fallback to legacy shared key (old format, backward compat)
        return self.config.get(section, {}).get("api_key", "")

    def set_provider_key(self, section: str, provider: str, key: str) -> None:
        """Set API key for a specific provider."""
        self.config.setdefault(section, {}).setdefault("keys", {})[provider] = key

    def get_reference_lang(self) -> str:
        """Return the fix_context reference language ('auto' or a code like 'pt-BR')."""
        return self.config.get("fix_context", {}).get("reference_lang", "fr")

    def set_reference_lang(self, lang: str) -> None:
        """Set the fix_context reference language."""
        self.config.setdefault("fix_context", {})["reference_lang"] = lang
