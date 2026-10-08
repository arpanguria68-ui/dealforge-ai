import pytest


class _FakeLaya:
    def __init__(self, tier):
        self.tier = tier
        self.calls = 0

    async def route_tier(self, _text):
        self.calls += 1
        return self.tier


class _FakeSettings:
    def __init__(self, laya):
        self.values = {"laya": laya}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.mark.asyncio
async def test_laya_tier_selects_configured_model_for_routed_provider(monkeypatch):
    from app.core.llm.model_router import ModelRouter
    from app.core.settings_service import SettingsService
    from app.core.laya import client as laya_client

    classifier = _FakeLaya("complex")
    monkeypatch.setattr(laya_client, "get_laya_client", lambda: classifier)
    monkeypatch.setattr(
        SettingsService,
        "get_instance",
        lambda: _FakeSettings({"reasoning_model": "gemini:gemini-pro-custom"}),
    )

    router = ModelRouter()
    router.agent_routing["test_agent"] = "gemini"

    async def first_viable(pool, _est_tokens=500):
        assert pool[0] == "gemini"
        return "gemini"

    monkeypatch.setattr(router, "_first_viable", first_viable)

    provider, model, fallback = await router.get_model_route_for_text(
        "test_agent", "perform detailed valuation sensitivity analysis"
    )

    assert (provider, model, fallback) == ("gemini", "gemini-pro-custom", False)
    assert classifier.calls == 1


@pytest.mark.asyncio
async def test_tier_model_override_is_ignored_when_provider_does_not_match(monkeypatch):
    from app.core.llm.model_router import ModelRouter
    from app.core.settings_service import SettingsService
    from app.core.laya import client as laya_client

    classifier = _FakeLaya("complex")
    monkeypatch.setattr(laya_client, "get_laya_client", lambda: classifier)
    monkeypatch.setattr(
        SettingsService,
        "get_instance",
        lambda: _FakeSettings({"reasoning_model": "openai:o3-custom"}),
    )

    router = ModelRouter()
    router.agent_routing["test_agent"] = "gemini"
    async def no_viable(_pool, _est_tokens=500):
        return None

    monkeypatch.setattr(router, "_first_viable", no_viable)

    provider, model, _ = await router.get_model_route_for_text("test_agent", "deep analysis")

    assert provider == "gemini"
    assert model is None


@pytest.mark.asyncio
async def test_document_agent_completion_uses_laya_route_and_gateway_fallback(monkeypatch):
    from app.agents.business_analyst import BusinessAnalystAgent
    import app.agents.base as base_module

    class FakeRouter:
        async def get_model_route_for_text(self, agent, task, est_tokens=None):
            assert agent == "business_analyst"
            assert "business_analyst" in task
            return "mistral", "mistral-large-custom", True

    class FakeGateway:
        async def call(self, **kwargs):
            assert kwargs["provider"] == "mistral"
            assert kwargs["model"] == "mistral-large-custom"
            assert kwargs["json_mode"] is True
            return {"content": "{}", "provider_used": "mistral"}

    agent = object.__new__(BusinessAnalystAgent)
    agent.name = "business_analyst"
    agent._llm_client_injected = False
    agent.logger = type("Logger", (), {"warning": lambda *args, **kwargs: None})()
    monkeypatch.setattr(base_module, "get_model_router", lambda: FakeRouter())
    monkeypatch.setattr(base_module, "get_llm_gateway", lambda: FakeGateway())

    result = await agent.generate_with_routed_fallback("write a sourced report", "system")
    assert result["content"] == "{}"
    assert agent._last_llm_provider == "mistral"


@pytest.mark.asyncio
async def test_local_only_issue_tree_disables_gateway_fallback(monkeypatch):
    import app.agents.base as base_module

    class FakeGateway:
        async def call(self, **kwargs):
            assert kwargs["provider"] == "lmstudio"
            assert kwargs["allow_fallback"] is False
            return {"content": '{"hypothesis":"Assess","branches":[]}'}

    class FakeRouter:
        def get_provider_for_agent(self, _agent):
            return "lmstudio"

    from app.agents.business_analyst import BusinessAnalystAgent

    agent = object.__new__(BusinessAnalystAgent)
    agent.name = "risk_assessor"
    agent.logger = type("Logger", (), {"info": lambda *args, **kwargs: None})()
    monkeypatch.setattr(base_module, "get_model_router", lambda: FakeRouter())
    monkeypatch.setattr(base_module, "get_llm_gateway", lambda: FakeGateway())

    tree = await agent.generate_issue_tree(
        "Assess synthetic case", {"routed_provider": "lmstudio", "local_only": True}
    )

    assert tree.hypothesis == "Assess"


@pytest.mark.asyncio
async def test_local_only_tool_generation_disables_gateway_fallback(monkeypatch):
    import app.agents.base as base_module

    class FakeTools:
        def list_tools(self, agent_name=None):
            return []

    class FakeGateway:
        async def call(self, **kwargs):
            assert kwargs["provider"] == "lmstudio"
            assert kwargs["allow_fallback"] is False
            return {"content": "local result", "provider_used": "lmstudio"}

    from app.agents.business_analyst import BusinessAnalystAgent

    agent = object.__new__(BusinessAnalystAgent)
    agent.name = "risk_assessor"
    agent.tools = FakeTools()
    agent._current_context = {"routed_provider": "lmstudio", "local_only": True}
    agent.logger = type("Logger", (), {"info": lambda *args, **kwargs: None})()
    monkeypatch.setattr(base_module, "get_model_router", lambda: object())
    monkeypatch.setattr(base_module, "get_llm_gateway", lambda: FakeGateway())

    result = await agent.generate_with_tools("Assess the supplied case", "JSON only")

    assert result["content"] == "local result"
