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
        assert "deepseek" in providers
        assert len(providers) == 4

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
        api = APIFactory.create(
            "libretranslate",
            "optional-key",
            url="https://example.com",
        )
        assert api.get_name() == "LibreTranslate"
        assert api.url == "https://example.com"
        assert api.api_key == "optional-key"

    def test_public_libretranslate_requires_key(self):
        with pytest.raises(ValueError, match="requires an API key"):
            APIFactory.create(
                "libretranslate",
                url="https://libretranslate.com",
            )

    def test_keyed_provider_requires_key(self):
        with pytest.raises(ValueError, match="API key required for groq"):
            APIFactory.create("groq")

    def test_create_groq(self):
        api = APIFactory.create("groq", "fake-key")
        assert "Groq" in api.get_name()

    def test_create_with_model(self):
        api = APIFactory.create("groq", "fake-key", model="llama-3.1-8b-instant")
        # GroqAPI doesn't expose model in get_name(); just verify creation works
        assert "Groq" in api.get_name()

    @pytest.mark.parametrize(
        ("api_type", "provider", "section", "model"),
        [
            ("free", "groq", "free_api", "openai/gpt-oss-20b"),
            ("paid", "deepseek", "paid_api", "deepseek-v4-pro"),
        ],
    )
    def test_create_from_settings_uses_provider_specific_model(
        self,
        monkeypatch,
        api_type,
        provider,
        section,
        model,
    ):
        captured = {}

        class FakeSettings:
            def get_api_type(self):
                return api_type

            def get_free_provider(self):
                return provider

            def get_paid_provider(self):
                return provider

            def get_provider_key(self, requested_section, requested_provider):
                assert requested_section == section
                assert requested_provider == provider
                return "key"

            def get_provider_model(self, requested_section, requested_provider):
                assert requested_section == section
                assert requested_provider == provider
                return model

        def fake_create(requested_provider, api_key="", **kwargs):
            captured.update(
                provider=requested_provider,
                api_key=api_key,
                model=kwargs.get("model"),
            )
            return captured

        monkeypatch.setattr(APIFactory, "create", staticmethod(fake_create))

        result = APIFactory.create_from_settings(FakeSettings())

        assert result == {
            "provider": provider,
            "api_key": "key",
            "model": model,
        }
