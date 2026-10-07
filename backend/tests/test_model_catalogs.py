import pytest
import json


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_gemini_catalog_uses_current_endpoint_filters_retired_and_deduplicates(monkeypatch):
    from app import main

    observed = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, **kwargs):
            observed.update(url=url, **kwargs)
            return FakeResponse(200, {"models": [
                {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "inputTokenLimit": 1048576, "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "inputTokenLimit": 1048576, "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.0-flash-001", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-1.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemma-4-31b-it", "supportedGenerationMethods": ["generateContent"]},
            ]})

    monkeypatch.setattr(main._httpx, "AsyncClient", Client)

    result = await main._fetch_gemini_models("test-key")

    assert result["status"] == "online"
    assert [model["id"] for model in result["models"]] == [
        "gemini-3.8-flash",
        "gemini-2.5-flash",
    ]
    assert observed["url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert observed["headers"] == {"x-goog-api-key": "test-key"}
    assert observed["params"] == {"pageSize": 100}


@pytest.mark.asyncio
async def test_nvidia_catalog_uses_openai_compatible_models_endpoint(monkeypatch):
    from app import main

    observed = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, **kwargs):
            observed.update(url=url, **kwargs)
            return FakeResponse(200, {"data": [
                {"id": "z-ai/glm-5.3"},
                {"id": "deepseek-ai/deepseek-v4-flash"},
                {"id": "z-ai/glm-5.3"},
            ]})

    monkeypatch.setattr(main._httpx, "AsyncClient", Client)

    result = await main._fetch_nvidia_models(
        "test-key", "https://integrate.api.nvidia.com/v1/"
    )

    assert result["status"] == "online"
    assert [model["id"] for model in result["models"]] == [
        "deepseek-ai/deepseek-v4-flash",
        "z-ai/glm-5.3",
    ]
    assert observed["url"] == "https://integrate.api.nvidia.com/v1/models"
    assert observed["headers"] == {"Authorization": "Bearer test-key"}


@pytest.mark.asyncio
async def test_nvidia_catalog_does_not_call_api_without_key():
    from app import main

    result = await main._fetch_nvidia_models("", "https://integrate.api.nvidia.com/v1")

    assert result == {"status": "no_key", "models": []}


@pytest.mark.asyncio
async def test_openrouter_catalog_returns_live_model_metadata(monkeypatch):
    from app import main

    observed = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, **kwargs):
            observed.update(url=url, **kwargs)
            return FakeResponse(200, {"data": [
                {"id": "vendor/model", "name": "Vendor Model", "context_length": 65536,
                 "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
                {"id": "vendor/model", "name": "Duplicate"},
            ]})

    monkeypatch.setattr(main._httpx, "AsyncClient", Client)
    result = await main._fetch_openrouter_models("test-key")

    assert result["status"] == "online"
    assert result["models"] == [{
        "id": "vendor/model", "name": "Vendor Model", "context_window": 65536,
        "prompt_price": "0.000001", "completion_price": "0.000002",
    }]
    assert observed["url"] == "https://openrouter.ai/api/v1/models"
    assert observed["headers"] == {"Authorization": "Bearer test-key"}


@pytest.mark.asyncio
async def test_openrouter_catalog_does_not_call_api_without_key():
    from app import main

    assert await main._fetch_openrouter_models("") == {"status": "no_key", "models": []}


@pytest.mark.asyncio
async def test_available_models_includes_openrouter_provider(monkeypatch):
    from app import main

    class Settings:
        def get(self, _key, default=""):
            return default

    monkeypatch.setattr("app.core.settings_service.SettingsService.get_instance", lambda: Settings())
    monkeypatch.setattr(main, "_fetch_gemini_models", lambda *_args, **_kwargs: _offline_catalog())
    monkeypatch.setattr(main, "_fetch_mistral_models", lambda *_args, **_kwargs: _offline_catalog())
    monkeypatch.setattr(main, "_fetch_openai_models", lambda *_args, **_kwargs: _offline_catalog())
    monkeypatch.setattr(main, "_fetch_openrouter_models", lambda *_args, **_kwargs: _offline_catalog())
    monkeypatch.setattr(main, "_fetch_vertex_models", lambda *_args, **_kwargs: _offline_catalog())
    monkeypatch.setattr(main, "_fetch_nvidia_models", lambda *_args, **_kwargs: _offline_catalog())
    monkeypatch.setattr(main, "_fetch_ollama_models", lambda *_args, **_kwargs: _offline_catalog())
    monkeypatch.setattr(main, "_fetch_lmstudio_models", lambda *_args, **_kwargs: _offline_catalog())

    result = await main.get_available_models(None)

    assert result["openrouter"] == {"status": "offline", "models": []}


async def _offline_catalog():
    return {"status": "offline", "models": []}


@pytest.mark.asyncio
async def test_openrouter_provider_test_endpoint_runs_catalog_and_generation(monkeypatch):
    from app import main

    class Settings:
        def get(self, key, default=None):
            return "vendor/model" if key == "openrouter_model" else default

    class Request:
        async def json(self):
            return {"provider": "openrouter", "api_key": "test-key", "model": "vendor/model"}

    monkeypatch.setattr("app.core.settings_service.SettingsService.get_instance", lambda: Settings())
    monkeypatch.setattr(main, "_fetch_openrouter_models", lambda *_args: _online_catalog())

    async def probe(provider, model, api_key, **_kwargs):
        assert (provider, model, api_key) == ("openrouter", "vendor/model", "test-key")
        return {"ok": True, "model": model, "latency_ms": 5}

    monkeypatch.setattr("app.core.llm.provider_probe.probe_provider", probe)

    result = await main.test_cloud_model(Request(), None)

    assert result["ok"] is True
    assert result["catalog_status"] == "online"
    assert result["generation"]["model"] == "vendor/model"


async def _online_catalog():
    return {"status": "online", "models": [{"id": "vendor/model", "name": "Vendor Model"}]}


def test_settings_load_migrates_retired_models_and_preserves_other_values(tmp_path):
    from app.core.settings_service import SettingsService

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "gemini_model": "gemini-1.5-flash",
        "vertex_model": "gemini-3.1-flash-lite-preview",
        "nvidia_model": "z-ai/glm5",
        "nvidia_api_key": "preserve-this-value",
    }))

    service = SettingsService(str(settings_path))

    assert service.get("gemini_model") == "gemini-3.8-flash"
    assert service.get("vertex_model") == "gemini-3.1-flash-lite"
    assert service.get("nvidia_model") == "z-ai/glm-5.3"
    assert service.get("nvidia_api_key") == "preserve-this-value"
    assert json.loads(settings_path.read_text())["gemini_model"] == "gemini-3.8-flash"
