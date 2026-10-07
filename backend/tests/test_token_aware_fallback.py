from types import SimpleNamespace

import pytest


def test_gateway_orders_fallback_by_request_size(monkeypatch):
    from app.core.llm import llm_gateway, model_router

    router = SimpleNamespace(
        fast_pool=["fast-a", "fast-b"],
        general_pool=["general-a"],
        reasoning_pool=["reason-a"],
    )
    monkeypatch.setattr(model_router, "get_model_router", lambda: router)
    gateway = llm_gateway.LLMGateway()

    assert gateway._fallback_candidates("gemini", 1_000)[:2] == ["fast-a", "fast-b"]
    assert gateway._fallback_candidates("gemini", 8_000)[:2] == ["general-a", "reason-a"]
    assert gateway._fallback_candidates("gemini", 20_000)[:2] == ["reason-a", "general-a"]
    assert "lmstudio" in gateway._fallback_candidates("gemini", 20_000)


def test_lmstudio_fallback_prefers_configured_openrouter_pool(monkeypatch):
    from app.core.llm import llm_gateway, model_router

    router = SimpleNamespace(
        fast_pool=["gemini"],
        general_pool=["openrouter", "nvidia"],
        reasoning_pool=["vertex"],
    )
    monkeypatch.setattr(model_router, "get_model_router", lambda: router)
    gateway = llm_gateway.LLMGateway()

    candidates = gateway._fallback_candidates("lmstudio", 10_000)
    assert candidates.index("openrouter") < candidates.index("nvidia")


def test_runtime_model_selection_overrides_stale_environment_model(monkeypatch):
    from app.core.llm import model_router

    live_models = {
        "openrouter_model": "nvidia/nemotron-3-super-120b-a12b:free",
    }
    fake_settings_service = SimpleNamespace(get=lambda key, default=None: live_models.get(key, default))
    monkeypatch.setattr(
        model_router.SettingsService,
        "get_instance",
        lambda: fake_settings_service,
    )

    model = model_router.get_configured_model(
        "openrouter", SimpleNamespace(OPENROUTER_MODEL="openai/gpt-4o-mini")
    )
    assert model == "nvidia/nemotron-3-super-120b-a12b:free"


def test_selected_openrouter_model_uses_its_advertised_context():
    from app.core.llm.model_registry import get_capabilities

    caps = get_capabilities("nvidia/nemotron-3-super-120b-a12b:free", "openrouter")
    assert caps.context_window == 262_144


@pytest.mark.asyncio
async def test_provider_readiness_rejects_insufficient_model_context(monkeypatch):
    from app.core.llm import llm_gateway, model_router

    class Router:
        async def check_local_health(self, _provider):
            return True

        def _provider_credentialed(self, _provider):
            return True

    monkeypatch.setattr(model_router, "get_model_router", lambda: Router())
    monkeypatch.setattr(
        llm_gateway,
        "get_settings",
        lambda: SimpleNamespace(LMSTUDIO_MODEL="local-model"),
    )
    gateway = llm_gateway.LLMGateway()

    assert await gateway._provider_ready("lmstudio", 8_000)
    assert not await gateway._provider_ready("lmstudio", 8_193)


@pytest.mark.asyncio
async def test_gateway_reports_fallback_model_not_original_model(monkeypatch):
    from app.core import llm
    from app.core.llm import llm_gateway, model_router

    settings = SimpleNamespace(
        LMSTUDIO_MODEL="local-model",
        NVIDIA_MODEL="nvidia-test-model",
    )

    class Client:
        def __init__(self, fails):
            self.fails = fails

        async def generate(self, **_kwargs):
            if self.fails:
                raise RuntimeError("simulated local generation failure")
            return {"content": "UAT_OK"}

    clients = {"lmstudio": Client(True), "nvidia": Client(False)}
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: settings)
    monkeypatch.setattr(
        model_router.SettingsService,
        "get_instance",
        lambda: SimpleNamespace(get=lambda _key, _default=None: None),
    )
    monkeypatch.setattr(llm, "get_llm_client", lambda provider: clients[provider])

    gateway = llm_gateway.LLMGateway()
    monkeypatch.setattr(gateway, "_provider_ready", _always_ready)
    monkeypatch.setattr(gateway, "_fallback_candidates", lambda *_args: ["nvidia"])

    result = await gateway.call(
        provider="lmstudio",
        prompt="Return UAT_OK",
        max_tokens=16,
        temperature=0,
        use_cache=False,
    )

    assert result["provider_used"] == "nvidia"
    assert result["model_used"] == "nvidia-test-model"
    assert result["fallback_used"] is True


@pytest.mark.asyncio
async def test_local_only_gateway_never_attempts_cloud_after_empty_local_response(monkeypatch):
    from app.core import llm
    from app.core.llm import llm_gateway, model_router

    class EmptyLocalClient:
        async def generate(self, **_kwargs):
            return {"content": ""}

    monkeypatch.setattr(
        llm_gateway,
        "get_settings",
        lambda: SimpleNamespace(LMSTUDIO_MODEL="local-model"),
    )
    monkeypatch.setattr(
        model_router.SettingsService,
        "get_instance",
        lambda: SimpleNamespace(get=lambda _key, _default=None: None),
    )
    monkeypatch.setattr(llm, "get_llm_client", lambda _provider: EmptyLocalClient())
    gateway = llm_gateway.LLMGateway()
    monkeypatch.setattr(gateway, "_provider_ready", _always_ready)

    def fail_if_fallback_is_considered(*_args):
        raise AssertionError("local-only generation must not enumerate cloud fallbacks")

    monkeypatch.setattr(gateway, "_fallback_candidates", fail_if_fallback_is_considered)

    with pytest.raises(RuntimeError, match="fallback is disabled"):
        await gateway.call(
            provider="lmstudio",
            prompt="Local-only UAT",
            max_tokens=16,
            use_cache=False,
            allow_fallback=False,
        )


async def _always_ready(_provider, _est_tokens):
    return True
