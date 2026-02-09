"""Gerenciador de configurações do aplicativo."""

import json
from pathlib import Path
from typing import Optional, Dict, Any


class Settings:
    """Gerencia as configurações persistentes do aplicativo."""

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "translation-automator"
        self.config_file = self.config_dir / "config.json"
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Carrega configurações do arquivo JSON."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return self._get_default_config()
        return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Retorna configuração padrão."""
        return {
            "api_type": "free",
            "free_api": {
                "provider": "groq",  # groq, gemini-free, deepl-free, libretranslate, openrouter, mistral-free
                "api_key": "",  # Para Groq, Gemini, DeepL, OpenRouter, Mistral
                "libretranslate_url": "https://libretranslate.com",
                "model": "llama-3.3-70b-versatile"  # Modelo padrão
            },
            "paid_api": {
                "provider": "openai",  # openai, gemini, claude, grok
                "api_key": "",
                "model": "gpt-4o-mini"
            }
        }

    def save(self):
        """Salva configurações no arquivo JSON."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None) -> Any:
        """Obtém valor de configuração."""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, default)
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        """Define valor de configuração."""
        keys = key.split('.')
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

    def get_paid_provider(self) -> str:
        """Retorna provider de API paga."""
        return self.config.get("paid_api", {}).get("provider", "openai")
