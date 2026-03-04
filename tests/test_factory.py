"""Tests for api.factory — provider creation and validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "usr" / "share" / "langforge"))

import pytest
from api.factory import APIFactory


class TestAPIFactory:
    def test_get_free_providers(self):
        providers = APIFactory.get_free_providers()
        assert "groq" in providers
        assert "deepl-free" in providers
        assert "libretranslate" in providers
        assert len(providers) == 6

    def test_get_paid_providers(self):
        providers = APIFactory.get_paid_providers()
        assert "openai" in providers
        assert "gemini" in providers
        assert "grok" in providers
        assert len(providers) == 3

    def test_is_valid_provider_free(self):
        assert APIFactory.is_valid_provider("groq")
        assert APIFactory.is_valid_provider("deepl-free")

    def test_is_valid_provider_paid(self):
        assert APIFactory.is_valid_provider("openai")
        assert APIFactory.is_valid_provider("grok")

    def test_is_valid_provider_invalid(self):
        assert not APIFactory.is_valid_provider("nonexistent")
        assert not APIFactory.is_valid_provider("")

    def test_create_unknown_provider(self):
        with pytest.raises(ValueError, match="desconhecido"):
            APIFactory.create("unknown_provider", "key123")

    def test_create_libretranslate(self):
        api = APIFactory.create("libretranslate", url="https://example.com")
        assert api.get_name() == "LibreTranslate"

    def test_create_groq(self):
        api = APIFactory.create("groq", "fake-key")
        assert "Groq" in api.get_name()

    def test_create_with_model(self):
        api = APIFactory.create("groq", "fake-key", model="llama-3.1-8b-instant")
        # GroqAPI doesn't expose model in get_name(); just verify creation works
        assert "Groq" in api.get_name()
