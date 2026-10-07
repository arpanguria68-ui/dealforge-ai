import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "expected_url", "expected_auth"),
    [
        (
            "massive",
            "https://api.massive.com/v1/marketstatus/now",
            {"headers": {"Authorization": "Bearer test-key"}},
        ),
        (
            "fmp",
            "https://financialmodelingprep.com/stable/profile?symbol=AAPL",
            {"params": {"apikey": "test-key"}},
        ),
    ],
)
async def test_provider_initialization_uses_current_health_endpoint(
    monkeypatch, provider_name, expected_url, expected_auth
):
    from app.core import mcp

    observed = {}

    class Response:
        status_code = 200
        text = "ok"

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, **kwargs):
            observed.update(url=url, **kwargs)
            return Response()

    monkeypatch.setattr(mcp.httpx, "AsyncClient", Client)
    monkeypatch.setitem(mcp._runtime_keys, provider_name, "old-key")

    result = await mcp.initialize_provider(provider_name, "test-key")

    assert result["ok"] is True
    actual_url = observed.pop("url")
    assert actual_url == expected_url
    assert {key: observed[key] for key in expected_auth} == expected_auth
    assert mcp._runtime_keys[provider_name] == "test-key"


@pytest.mark.asyncio
async def test_failed_provider_validation_does_not_retain_key_or_leak_secret(monkeypatch):
    from app.core import mcp

    secret = "test-secret-key"

    class Response:
        status_code = 403
        text = f"rejected {secret}"

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(mcp.httpx, "AsyncClient", Client)
    monkeypatch.setitem(mcp._runtime_keys, "fmp", "previous-key")

    result = await mcp.initialize_provider("fmp", secret)

    assert result["ok"] is False
    assert secret not in result["error"]
    assert "[redacted]" in result["error"]
    assert "fmp" not in mcp._runtime_keys




def test_llm_factory_passes_saved_provider_credentials_and_model(monkeypatch):
    import app.core.llm as llm

    captured = {}

    class FakeSettings:
        GEMINI_API_KEY = "env-fallback"
        GEMINI_MODEL = "env-model"

    class FakeRuntime:
        def get(self, key):
            return {"gemini_api_key": "saved-secret", "gemini_model": "saved-model"}.get(key)

    class FakeGemini:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(llm, "GeminiClient", FakeGemini)
    monkeypatch.setattr(
        "app.core.settings_service.SettingsService.get_instance",
        lambda: FakeRuntime(),
    )

    llm.get_llm_client("gemini")

    assert captured == {"model": "saved-model", "api_key": "saved-secret"}


def test_llm_factory_registers_openrouter(monkeypatch):
    import app.core.llm as llm

    captured = {}

    class FakeSettings:
        OPENROUTER_API_KEY = "env-fallback"
        OPENROUTER_MODEL = "env/model"

    class FakeRuntime:
        def get(self, key):
            return {"openrouter_api_key": "saved-secret", "openrouter_model": "vendor/model"}.get(key)

    class FakeOpenRouter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(llm, "OpenRouterClient", FakeOpenRouter)
    monkeypatch.setattr(
        "app.core.settings_service.SettingsService.get_instance",
        lambda: FakeRuntime(),
    )

    llm.get_llm_client("openrouter")

    assert captured == {"api_key": "saved-secret", "model": "vendor/model"}


def test_openrouter_client_uses_official_openai_compatible_endpoint(monkeypatch):
    from app.core.llm import openrouter_client

    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeSettings:
        OPENROUTER_API_KEY = "env-secret"
        OPENROUTER_MODEL = "vendor/model"

    monkeypatch.setattr(openrouter_client, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr("app.config.get_settings", lambda: FakeSettings())

    client = openrouter_client.OpenRouterClient()

    assert client.model == "vendor/model"
    assert client.provider == "openrouter"
    assert captured == {
        "api_key": "env-secret",
        "base_url": "https://openrouter.ai/api/v1",
        "default_headers": {"X-OpenRouter-Title": "DealForge AI"},
        "timeout": 60.0,
        "max_retries": 0,
    }


def test_openrouter_known_upstream_model_uses_registered_capabilities():
    from app.core.llm.model_registry import get_capabilities

    capabilities = get_capabilities("openai/gpt-4o-mini", "openrouter")

    assert capabilities.tool_calling is True
    assert capabilities.json_mode is True
    assert capabilities.context_window == 128_000


def test_openrouter_unknown_model_keeps_conservative_capabilities():
    from app.core.llm.model_registry import get_capabilities

    capabilities = get_capabilities("unknown/model", "openrouter")

    assert capabilities.tool_calling is False
    assert capabilities.json_mode is False
    assert capabilities.context_window == 32_768


@pytest.mark.asyncio
async def test_openrouter_client_sends_only_messages_and_requested_controls():
    from types import SimpleNamespace
    from app.core.llm.openrouter_client import OpenRouterClient

    captured = {}

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content="OK", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = OpenRouterClient.__new__(OpenRouterClient)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client.model = "vendor/test-model"

    result = await client.generate(
        prompt="synthetic test prompt",
        system_prompt="synthetic system instructions",
        tools=[{"type": "function", "function": {"name": "read_only_lookup"}}],
        temperature=0,
        max_tokens=32,
    )

    assert result["content"] == "OK"
    assert captured == {
        "model": "vendor/test-model",
        "messages": [
            {"role": "system", "content": "synthetic system instructions"},
            {"role": "user", "content": "synthetic test prompt"},
        ],
        "temperature": 0,
        "max_tokens": 32,
        "tools": [{"type": "function", "function": {"name": "read_only_lookup"}}],
        "tool_choice": "auto",
    }
