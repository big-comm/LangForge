"""Mocked request contracts for current AI provider models."""

from types import SimpleNamespace

import pytest
import requests

from api.base import BatchAlignmentError
from api.free_apis import (
    DeepLFreeAPI,
    GeminiFreeAPI,
    GroqAPI,
    LibreTranslateAPI,
    MistralFreeAPI,
    OpenRouterAPI,
)
from api.paid_apis import DeepSeekAPI, GeminiAPI, GrokAPI, OpenAIAPI


class StubHTTPResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class StubSession:
    def __init__(self, response_text="Olá"):
        self.response_text = response_text
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return StubHTTPResponse(
            {
                "choices": [{"message": {"content": self.response_text}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 20},
                },
            }
        )


class FakeCompletions:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **kwargs):
        self.owner.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.owner.response_text)
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                prompt_tokens_details=SimpleNamespace(cached_tokens=20),
                prompt_cache_hit_tokens=20,
                prompt_cache_miss_tokens=80,
            ),
        )


class FakeOpenAIClient:
    instances = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.response_text = "Olá"
        self.calls = []
        self.chat = SimpleNamespace(completions=FakeCompletions(self))
        self.models = SimpleNamespace(list=lambda: iter([{"id": "model"}]))
        self.instances.append(self)


class FakeGeminiModels:
    def __init__(self):
        self.response_text = "Olá"
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=self.response_text,
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=20,
                cached_content_token_count=20,
            ),
        )


class FakeGeminiClient:
    instances = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.models = FakeGeminiModels()
        self.instances.append(self)


def test_deepl_probe_timeout_does_not_switch_account_endpoint():
    class TimeoutSession:
        def get(self, *_args, **_kwargs):
            raise requests.Timeout("temporary timeout")

    api = DeepLFreeAPI("key:fx")
    api.session = TimeoutSession()

    with pytest.raises(requests.Timeout):
        api._ensure_endpoint()

    assert api.base_url == "https://api-free.deepl.com/v2"
    assert api._resolved is False


def test_deepl_current_language_map_includes_hebrew():
    assert DeepLFreeAPI.DEEPL_LANG_MAP["he"] == "HE"


def test_openrouter_connection_uses_authenticated_key_endpoint():
    class RecordingSession:
        def __init__(self):
            self.url = ""
            self.headers = {}

        def get(self, url, **kwargs):
            self.url = url
            self.headers = kwargs["headers"]
            return StubHTTPResponse({})

    api = OpenRouterAPI("secret")
    api.session = RecordingSession()

    assert api.test_connection() is True
    assert api.session.url == "https://openrouter.ai/api/v1/key"
    assert api.session.headers["Authorization"] == "Bearer secret"


def test_libretranslate_sends_optional_api_key_and_tests_translation():
    class LibreSession:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return StubHTTPResponse({"translatedText": "prueba"})

    api = LibreTranslateAPI("https://example.test", "secret")
    api.session = LibreSession()

    assert api.test_connection() is True
    url, request = api.session.calls[0]
    assert url == "https://example.test/translate"
    assert request["json"] == {
        "q": "test",
        "source": "en",
        "target": "es",
        "format": "text",
        "api_key": "secret",
    }


@pytest.fixture
def fake_openai(monkeypatch):
    import openai

    FakeOpenAIClient.instances = []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAIClient)
    return FakeOpenAIClient


@pytest.fixture
def fake_gemini(monkeypatch):
    from google import genai

    FakeGeminiClient.instances = []
    monkeypatch.setattr(genai, "Client", FakeGeminiClient)
    return FakeGeminiClient


def test_groq_uses_gpt_oss_contract():
    api = GroqAPI("key")
    api.session = StubSession()

    assert api.translate("Hello", "en", "pt") == "Olá"

    _, request = api.session.calls[0]
    payload = request["json"]
    assert payload["model"] == "openai/gpt-oss-120b"
    assert payload["reasoning_effort"] == "low"
    assert payload["include_reasoning"] is False
    assert payload["max_completion_tokens"] == 512
    assert "max_tokens" not in payload
    assert api.get_usage()["api_calls"] == 1
    assert api.get_usage()["cost_usd"] == 0


def test_openrouter_uses_stable_free_gpt_oss_and_router_fallback():
    api = OpenRouterAPI("key")
    api.session = StubSession()

    api.translate("Hello", "en", "pt")

    _, request = api.session.calls[0]
    assert request["json"]["model"] == "openai/gpt-oss-120b:free"
    assert request["json"]["reasoning"] == {
        "effort": "low",
        "exclude": True,
    }
    assert request["headers"]["X-Title"] == "LangForge"
    assert api.get_usage()["api_calls"] == 1
    assert api.get_usage()["cost_usd"] == 0

    fallback = OpenRouterAPI("key", "openrouter/free")
    assert "reasoning" not in fallback._payload([], 512)


def test_mistral_small_4_disables_reasoning():
    api = MistralFreeAPI("key")
    api.session = StubSession()

    api.translate("Hello", "en", "pt")

    _, request = api.session.calls[0]
    assert request["json"]["model"] == "mistral-small-latest"
    assert request["json"]["reasoning_effort"] == "none"
    assert request["json"]["max_tokens"] == 512


def test_openai_uses_gpt5_chat_contract(fake_openai):
    api = OpenAIAPI("key")

    assert api.translate("Hello", "en", "pt") == "Olá"

    client = fake_openai.instances[0]
    request = client.calls[0]
    assert request["model"] == "gpt-5.6-luna"
    assert request["messages"][0]["role"] == "developer"
    assert request["reasoning_effort"] == "none"
    assert request["max_completion_tokens"] == 512
    assert "temperature" not in request
    assert "max_tokens" not in request
    assert api.get_usage()["input_tokens"] == 100


@pytest.mark.parametrize(
    ("api_class", "model", "thinking_level"),
    [
        (GeminiFreeAPI, "gemini-3.5-flash-lite", "minimal"),
        (GeminiAPI, "gemini-3.5-flash-lite", "minimal"),
        (GeminiAPI, "gemini-3.6-flash", "minimal"),
    ],
)
def test_gemini_3_removes_sampling_parameters(
    fake_gemini,
    api_class,
    model,
    thinking_level,
):
    api = api_class("key", model)

    assert api.translate("Hello", "en", "pt") == "Olá"

    client = fake_gemini.instances[-1]
    request = client.models.calls[0]
    assert request["model"] == model
    assert request["contents"] == "Hello"
    assert "professional translator" in request["config"]["system_instruction"]
    assert request["config"]["thinking_config"] == {"thinking_level": thinking_level}
    assert request["config"]["max_output_tokens"] == 512
    assert "temperature" not in request["config"]
    assert "top_p" not in request["config"]
    assert "top_k" not in request["config"]
    assert api.get_usage()["api_calls"] == 1
    if api_class is GeminiFreeAPI:
        assert api.get_usage()["cost_usd"] == 0
    else:
        assert api.get_usage()["cost_usd"] > 0


def test_paid_gemini_uncached_sdk_usage_treats_none_as_zero(fake_gemini):
    from google.genai import types

    api = GeminiAPI("key")
    metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=100,
        candidates_token_count=20,
    )

    api._track_gemini_response(SimpleNamespace(usage_metadata=metadata))

    usage = api.get_usage()
    assert metadata.cached_content_token_count is None
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["api_calls"] == 1
    assert usage["cost_usd"] > 0


def test_deepseek_v4_disables_thinking(fake_openai):
    api = DeepSeekAPI("key")

    assert api.translate("Hello", "en", "pt") == "Olá"

    client = fake_openai.instances[0]
    assert client.init_kwargs["base_url"] == "https://api.deepseek.com"
    request = client.calls[0]
    assert request["model"] == "deepseek-v4-flash"
    assert request["temperature"] == 1.3
    assert request["max_tokens"] == 512
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert api.get_usage()["input_tokens"] == 100


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        ("grok-4.3", "none"),
        ("grok-4.5", "low"),
    ],
)
def test_grok_uses_supported_reasoning_effort(model, effort):
    api = GrokAPI("key", model)
    api.session = StubSession()

    api.translate("Hello", "en", "pt")

    _, request = api.session.calls[0]
    assert request["json"]["model"] == model
    assert request["json"]["reasoning_effort"] == effort
    assert request["json"]["max_tokens"] == 512


@pytest.mark.parametrize(
    "api_class",
    [
        GroqAPI,
        GeminiFreeAPI,
        OpenRouterAPI,
        MistralFreeAPI,
        OpenAIAPI,
        GeminiAPI,
        GrokAPI,
        DeepSeekAPI,
    ],
)
def test_item_instruction_reaches_every_provider_system_prompt(
    fake_openai,
    fake_gemini,
    api_class,
):
    api = api_class("key")
    assert api.supports_item_instructions is True
    assert api.supports_context is True
    if isinstance(api, (GroqAPI, OpenRouterAPI, MistralFreeAPI, GrokAPI)):
        api.session = StubSession()

    instruction = "Use gettext plural form 2 for example count 5."
    assert api.translate_with_instruction("Files", "en", "ru", instruction) == "Olá"

    if isinstance(api, (GeminiFreeAPI, GeminiAPI)):
        prompt = fake_gemini.instances[-1].models.calls[-1]["config"][
            "system_instruction"
        ]
    elif isinstance(api, (OpenAIAPI, DeepSeekAPI)):
        prompt = fake_openai.instances[-1].calls[-1]["messages"][0]["content"]
    else:
        _url, request = api.session.calls[-1]
        prompt = request["json"]["messages"][0]["content"]

    assert instruction in prompt
    assert not hasattr(api, "_item_instruction")


@pytest.mark.parametrize(
    "api",
    [
        LibreTranslateAPI("https://example.test", "key"),
        DeepLFreeAPI("key:fx"),
    ],
)
def test_plain_machine_translation_declares_no_context_or_instruction_support(api):
    assert api.supports_item_instructions is False
    assert api.supports_context is False

    with pytest.raises(NotImplementedError, match="does not support"):
        api.translate_with_instruction(
            "Files",
            "en",
            "ru",
            "Use plural form 2",
        )


@pytest.mark.parametrize(
    "api_class",
    [
        GroqAPI,
        GeminiFreeAPI,
        OpenRouterAPI,
        MistralFreeAPI,
        OpenAIAPI,
        GeminiAPI,
        GrokAPI,
        DeepSeekAPI,
    ],
)
@pytest.mark.parametrize(
    "malformed_response",
    [
        "[1] Only the first translation",
        "[1] First|||NEXT|||[1] Duplicate first",
    ],
    ids=["truncated", "duplicate"],
)
def test_batch_alignment_failure_is_delegated_to_the_engine(
    fake_openai,
    fake_gemini,
    api_class,
    malformed_response,
):
    api = api_class("key")
    if isinstance(api, (GroqAPI, OpenRouterAPI, MistralFreeAPI, GrokAPI)):
        api.session = StubSession(malformed_response)
        calls = api.session.calls
    elif isinstance(api, (OpenAIAPI, DeepSeekAPI)):
        api.client.response_text = malformed_response
        calls = api.client.calls
    else:
        api.client.models.response_text = malformed_response
        calls = api.client.models.calls

    with pytest.raises(BatchAlignmentError):
        api.translate_batch(["First", "Second"], "en", "pt")

    assert len(calls) == 1
