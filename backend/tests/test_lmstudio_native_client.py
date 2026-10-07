from types import SimpleNamespace

import httpx
import pytest


class NativeResponse:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status
        self.status_code = status
        self.request = httpx.Request("POST", "http://localhost/api/v1/chat")

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("request failed", request=self.request, response=self)

    def json(self):
        return self.data


class NativeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class LegacyCompletions:
    def __init__(self, content="legacy answer"):
        self.calls = []
        self.content = content

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content, tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_lmstudio_plain_chat_uses_native_endpoint_and_reasoning_off():
    from app.core.llm.local_llm_client import LMStudioClient

    client = LMStudioClient.__new__(LMStudioClient)
    client.model = "qwen/qwen3.5-9b"
    client.base_url = "http://localhost:1234/v1"
    client.native_base_url = "http://localhost:1234"
    client.native_client = NativeClient(NativeResponse({
        "model_instance_id": "qwen/qwen3.5-9b",
        "output": [
            {"type": "reasoning", "content": "private reasoning"},
            {"type": "message", "content": "Visible answer"},
        ],
        "stats": {"reasoning_output_tokens": 0},
    }))
    client.reasoning = "off"
    client.provider = "lmstudio"

    result = await client.generate("prompt", "system", max_tokens=700)

    url, request = client.native_client.calls[0]
    assert url == "http://localhost:1234/api/v1/chat"
    assert request["json"]["reasoning"] == "off"
    assert request["json"]["system_prompt"] == "system"
    assert request["json"]["max_output_tokens"] == 700
    assert result["content"] == "Visible answer"
    assert "private reasoning" not in result["content"]
    assert result["provider_used"] == "lmstudio"


@pytest.mark.asyncio
async def test_lmstudio_empty_native_answer_is_not_reported_as_success():
    from app.core.llm.local_llm_client import LMStudioClient

    client = LMStudioClient.__new__(LMStudioClient)
    client.model = "qwen/qwen3.5-9b"
    client.native_base_url = "http://localhost:1234"
    client.native_client = NativeClient(NativeResponse({
        "output": [{"type": "reasoning", "content": "internal only"}],
    }))
    client.reasoning = "on"

    with pytest.raises(ValueError, match="no user-facing message"):
        await client.generate("prompt", max_tokens=256)


@pytest.mark.asyncio
async def test_lmstudio_tool_requests_keep_openai_compatible_path():
    from app.core.llm.local_llm_client import LMStudioClient

    client = LMStudioClient.__new__(LMStudioClient)
    client.model = "qwen/qwen3.5-9b"
    client.native_base_url = "http://localhost:1234"
    client.native_client = NativeClient(NativeResponse({"output": []}))
    client.provider = "lmstudio"
    client.reasoning = "off"
    completions = LegacyCompletions()
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = await client.generate("prompt", tools=[{"type": "function"}])

    assert client.native_client.calls == []
    assert result["content"] == "legacy answer"
    assert completions.calls[0]["tools"] == [{"type": "function"}]


@pytest.mark.asyncio
async def test_lmstudio_older_server_falls_back_to_compat_endpoint():
    from app.core.llm.local_llm_client import LMStudioClient

    client = LMStudioClient.__new__(LMStudioClient)
    client.model = "qwen/qwen3.5-9b"
    client.native_base_url = "http://localhost:1234"
    client.native_client = NativeClient(NativeResponse({}, status=404))
    client.provider = "lmstudio"
    client.reasoning = "off"
    completions = LegacyCompletions("compat answer")
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = await client.generate("prompt")

    assert len(client.native_client.calls) == 1
    assert result["content"] == "compat answer"
    assert completions.calls[0]["model"] == "qwen/qwen3.5-9b"
