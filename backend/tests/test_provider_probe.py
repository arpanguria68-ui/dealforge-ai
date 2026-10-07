import asyncio

import pytest

from app.core.llm import provider_probe
from app.core.llm import llm_gateway as gateway_module


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def generate(self, **kwargs):
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_probe_accepts_nonempty_completion(monkeypatch):
    monkeypatch.setattr(provider_probe, "_client", lambda *args: FakeClient({"content": "OK"}))

    result = await provider_probe.probe_provider("gemini", "test-model", "not-returned")

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["output_characters"] == 2
    assert "content" not in result
    assert "not-returned" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    ["qwen/qwen3.8-27b:free", "nvidia/nemotron-3-super-120b-a12b:free"],
)
async def test_probe_allows_openrouter_models_more_output_tokens(monkeypatch, model):
    captured = {}

    class Client:
        async def generate(self, **kwargs):
            captured.update(kwargs)
            return {"content": "OK"}

    monkeypatch.setattr(provider_probe, "_client", lambda *args: Client())

    result = await provider_probe.probe_provider(
        "openrouter", model, "not-returned"
    )

    assert result["ok"] is True
    assert captured["max_tokens"] == 128


def test_probe_constructs_openrouter_client(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.core.llm.openrouter_client.OpenRouterClient", Client)

    from app.core.llm.provider_probe import _client

    _client("openrouter", "provider/model", "secret")

    assert captured == {"model": "provider/model", "api_key": "secret"}


@pytest.mark.parametrize("provider", ["claude", "groq"])
def test_probe_constructs_additional_cloud_clients(monkeypatch, provider):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    if provider == "claude":
        monkeypatch.setattr("app.core.llm.claude_client.ClaudeClient", Client)
    else:
        monkeypatch.setattr("app.core.llm.groq_client.GroqClient", Client)

    from app.core.llm.provider_probe import _client

    _client(provider, "test-model", "secret")

    assert captured == {"api_key": "secret", "model": "test-model"}


@pytest.mark.asyncio
async def test_probe_rejects_empty_completion(monkeypatch):
    monkeypatch.setattr(provider_probe, "_client", lambda *args: FakeClient({"content": "  "}))

    result = await provider_probe.probe_provider("gemini", "test-model", "secret")

    assert result["ok"] is False
    assert result["status"] == "empty_response"


@pytest.mark.asyncio
async def test_probe_sanitizes_provider_exception(monkeypatch):
    monkeypatch.setattr(
        provider_probe,
        "_client",
        lambda *args: FakeClient(error=RuntimeError("request included secret-value")),
    )

    result = await provider_probe.probe_provider("gemini", "test-model", "secret-value")

    assert result["ok"] is False
    assert "secret-value" not in str(result)
    assert "RuntimeError" in result["error"]


@pytest.mark.asyncio
async def test_vertex_provider_log_never_includes_api_key_from_request_url(monkeypatch):
    import httpx
    from app.core.llm import vertex_client

    secret = "vertex-test-secret"
    captured = {}
    request = httpx.Request("POST", f"https://example.invalid/generate?key={secret}")
    failure = httpx.HTTPStatusError(
        "forbidden", request=request, response=httpx.Response(403, request=request)
    )

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, *_args, **_kwargs):
            raise failure

    class Settings:
        VERTEX_API_KEY = secret
        VERTEX_MODEL = "model"
        VERTEX_PROJECT_ID = ""
        VERTEX_LOCATION = "us-central1"

    monkeypatch.setattr(vertex_client.httpx, "AsyncClient", Client)
    monkeypatch.setattr(vertex_client, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        vertex_client.logger,
        "error",
        lambda event, **fields: captured.update(event=event, **fields),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await vertex_client.VertexClient().generate("test")

    assert secret not in str(captured)
    assert captured["status_code"] == 403


@pytest.mark.asyncio
async def test_probe_times_out(monkeypatch):
    async def slow_generate(self, **kwargs):
        await asyncio.sleep(0.05)

    monkeypatch.setattr(provider_probe, "_client", lambda *args: FakeClient())
    monkeypatch.setattr(FakeClient, "generate", slow_generate)

    result = await provider_probe.probe_provider(
        "gemini", "test-model", "secret", timeout_seconds=0.001
    )

    assert result["ok"] is False
    assert result["error"] == "The model did not respond before the test timeout."


@pytest.mark.asyncio
async def test_gateway_falls_back_after_empty_completion(monkeypatch):
    class GatewayClient:
        def __init__(self, content):
            self.content = content

        async def generate(self, **kwargs):
            return {"content": self.content}

    clients = {"lmstudio": GatewayClient(" "), "ollama": GatewayClient("usable result")}
    monkeypatch.setattr("app.core.llm.get_llm_client", lambda provider: clients[provider])

    async def no_retry_delay(fn):
        return await fn()

    monkeypatch.setattr(gateway_module, "exponential_backoff_retry", no_retry_delay)
    gateway = gateway_module.LLMGateway()

    async def only_supplied_fake_clients(provider, _est_tokens):
        return provider in clients

    monkeypatch.setattr(gateway, "_provider_ready", only_supplied_fake_clients)
    result = await gateway.call(
        provider="lmstudio", prompt="generic test", temperature=0.2, use_cache=False
    )

    assert result["content"] == "usable result"
    assert result["provider_used"] == "ollama"
    assert result["fallback_used"] is True


@pytest.mark.asyncio
async def test_gateway_does_not_backoff_retry_provider_quota_429(monkeypatch):
    class QuotaError(Exception):
        status_code = 429

    calls = 0

    async def rejected():
        nonlocal calls
        calls += 1
        raise QuotaError("provider quota exhausted")

    with pytest.raises(QuotaError):
        await gateway_module.exponential_backoff_retry(rejected)
    assert calls == 1


def test_settings_redact_placeholder_and_preserve_saved_secret(tmp_path):
    from app.core.settings_service import SettingsService

    service = SettingsService(str(tmp_path / "settings.json"))
    service._settings["gemini_api_key"] = "placeholder_key"
    service._settings["openai_api_key"] = "test-secret-value"
    service._apply_to_system = lambda: None

    redacted = service.get_all()
    assert redacted["gemini_api_key"] == ""
    assert redacted["gemini_api_key_configured"] is False
    assert redacted["openai_api_key"] == ""
    assert redacted["openai_api_key_configured"] is True

    service.update({"openai_api_key": "***"})
    assert service.get("openai_api_key") == "test-secret-value"
