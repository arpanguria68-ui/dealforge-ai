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

    class Gateway:
        async def call(self, **kwargs):
            calls.append(kwargs)
            return {
                "content": "437",
                "provider_used": "lmstudio",
                "model_used": "qwen/qwen3.5-9b",
                "tokens_est": 123,
                "fallback_used": False,
            }

    monkeypatch.setattr("app.core.llm.model_router.get_model_router", lambda: Router())
    monkeypatch.setattr("app.core.laya.client.get_laya_client", lambda: Laya())
    monkeypatch.setattr("app.core.llm.llm_gateway.get_llm_gateway", lambda: Gateway())

    result = await main.chat_respond(Request({"prompt": "What is 19 × 23?"}))

    assert result["response"] == "437"
    assert result["provider"] == "lmstudio"
    assert result["model"] == "qwen/qwen3.5-9b"
    assert result["tokens_est"] == 123
    assert result["used_fallback"] is False
    assert calls[0][0] == "business_analyst"
    assert "do not create a deal plan" in calls[1]["system_prompt"].lower()
    assert calls[1]["provider"] == "lmstudio"
    assert calls[1]["use_cache"] is False


@pytest.mark.asyncio
async def test_direct_chat_local_only_disables_gateway_fallback(monkeypatch):
    call = {}

    class Router:
        def get_provider_for_agent(self, _agent):
            return "lmstudio"

        async def check_local_health(self, _provider):
            return True

    class Gateway:
        async def call(self, **kwargs):
            call.update(kwargs)
            return {
                "content": "LOCAL_OK",
                "provider_used": "lmstudio",
                "model_used": "qwen/qwen3.5-9b",
                "tokens_est": 40,
                "fallback_used": False,
            }

    monkeypatch.setattr("app.core.llm.model_router.get_model_router", lambda: Router())
    monkeypatch.setattr("app.core.llm.llm_gateway.get_llm_gateway", lambda: Gateway())

    result = await main.chat_respond(Request({"prompt": "Explain briefly", "local_only": True}))

    assert result["response"] == "LOCAL_OK"
    assert call["provider"] == "lmstudio"
    assert call["allow_fallback"] is False


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
