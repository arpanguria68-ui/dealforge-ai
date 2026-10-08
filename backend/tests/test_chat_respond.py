from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main


class Request:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


@pytest.mark.asyncio
async def test_direct_chat_answers_without_creating_deal(monkeypatch):
    calls = []

    class Router:
        async def get_provider_for_text(self, agent, prompt, **kwargs):
            calls.append((agent, prompt, kwargs))
            return "lmstudio", False

    class Laya:
        async def route_tier(self, _prompt):
            return "simple"

    class Client:
        model = "local-test-model"

        async def generate(self, **kwargs):
            calls.append(kwargs)
            return {"content": "437"}

    monkeypatch.setattr("app.core.llm.model_router.get_model_router", lambda: Router())
    monkeypatch.setattr("app.core.laya.client.get_laya_client", lambda: Laya())
    monkeypatch.setattr("app.core.llm.get_llm_client", lambda _provider: Client())

    result = await main.chat_respond(Request({"prompt": "What is 19 × 23?"}))

    assert result["response"] == "437"
    assert result["provider"] == "lmstudio"
    assert result["model"] == "local-test-model"
    assert calls[0][0] == "business_analyst"
    assert "do not create a deal plan" in calls[1]["system_prompt"].lower()


@pytest.mark.asyncio
async def test_local_only_direct_chat_never_falls_back_to_cloud(monkeypatch):
    class Router:
        def get_provider_for_agent(self, _agent):
            return "nvidia"

        async def check_local_health(self, _provider):
            return False

    monkeypatch.setattr("app.core.llm.model_router.get_model_router", lambda: Router())

    with pytest.raises(HTTPException) as error:
        await main.chat_respond(Request({"prompt": "Explain this sentence", "local_only": True}))

    assert error.value.status_code == 503
    assert "cloud fallback is disabled" in error.value.detail
