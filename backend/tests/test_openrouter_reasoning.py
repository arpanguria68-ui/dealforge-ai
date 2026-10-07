from types import SimpleNamespace

import pytest


class FakeCompletions:
    def __init__(self, response):
        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _response(content, finish_reason="stop", reasoning_tokens=0):
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(
        completion_tokens=reasoning_tokens,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    return SimpleNamespace(choices=[choice], usage=usage)


@pytest.mark.asyncio
async def test_nemotron_openrouter_requests_low_reasoning_effort():
    from app.core.llm.openrouter_client import OpenRouterClient

    client = OpenRouterClient.__new__(OpenRouterClient)
    client.model = "nvidia/nemotron-3-super-120b-a12b:free"
    fake = FakeCompletions(_response('{"observation":"Revenue grew"}'))
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake),
    )

    result = await client.generate(
        prompt="Return a JSON observation.", json_mode=True, max_tokens=1024
    )

    assert fake.calls[0]["reasoning_effort"] == "low"
    assert result["content"] == '{"observation":"Revenue grew"}'


@pytest.mark.asyncio
async def test_nemotron_empty_length_completion_retries_with_minimal_reasoning():
    from app.core.llm.openrouter_client import OpenRouterClient

    client = OpenRouterClient.__new__(OpenRouterClient)
    client.model = "nvidia/nemotron-3-super-120b-a12b:free"
    fake = FakeCompletions([
        _response(None, finish_reason="length", reasoning_tokens=1024),
        _response('{"observation":"Revenue grew"}'),
    ])
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake),
    )

    result = await client.generate(prompt="Return JSON.", json_mode=True, max_tokens=1024)

    assert len(fake.calls) == 2
    assert fake.calls[0]["reasoning_effort"] == "low"
    assert fake.calls[1]["reasoning_effort"] == "minimal"
    assert result["content"] == '{"observation":"Revenue grew"}'


@pytest.mark.asyncio
async def test_nemotron_empty_retry_still_fails_safely():
    from app.core.llm.openrouter_client import OpenRouterClient

    client = OpenRouterClient.__new__(OpenRouterClient)
    client.model = "nvidia/nemotron-3-super-120b-a12b:free"
    fake = FakeCompletions([
        _response(None, finish_reason="length", reasoning_tokens=1024),
        _response(None, finish_reason="length", reasoning_tokens=1024),
    ])
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))

    with pytest.raises(ValueError, match="reasoning consumed the completion budget"):
        await client.generate(prompt="Return JSON.", json_mode=True, max_tokens=1024)
