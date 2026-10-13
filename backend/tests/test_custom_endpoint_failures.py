import asyncio
from types import SimpleNamespace

import pytest

from app.core.llm.gemini_client import OpenAIClient, looks_like_chat_failure


@pytest.mark.parametrize("text", [
    "Sorry, something went wrong. Please try your request again.",
    "I encountered an error doing what you asked. Could you try again?",
    "I'm sorry, I can't help with that.",
    "I'm having a hard time fulfilling your request right now. Please try again later.",
])
def test_canned_chat_errors_are_recognised(text):
    assert looks_like_chat_failure(text)


def test_real_answers_are_not_mistaken_for_failures():
    assert not looks_like_chat_failure("")
    assert not looks_like_chat_failure("Revenue grew 12.7% to $457.5M; the main risk is API dependence.")
    long_answer = "Risk section. " + "The company faces a rate limit on partner APIs. " * 20
    assert not looks_like_chat_failure(long_answer)   # long replies are never treated as canned errors


async def _no_sleep(*_args, **_kwargs):
    return None


def _client(replies):
    client = object.__new__(OpenAIClient)
    client.model, client.provider = "m", "custom_endpoint"
    calls = []

    async def create(**params):
        calls.append(params)
        text = replies[min(len(calls) - 1, len(replies) - 1)]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))])

    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, calls


def test_a_transient_canned_error_is_retried_once(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client, calls = _client(["Sorry, something went wrong. Please try again.", "The memo: recommend GO at $14."])
    result = asyncio.run(client.generate("write the memo"))
    assert result["content"].startswith("The memo") and len(calls) == 2


def test_a_persistent_canned_error_fails_instead_of_becoming_the_answer(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client, calls = _client(["Sorry, something went wrong. Please try again."])
    with pytest.raises(ValueError, match="instead of an answer"):
        asyncio.run(client.generate("write the memo"))
    assert len(calls) == 2


def test_a_one_line_reply_is_not_accepted_as_an_investment_memo():
    from app.agents.investment_memo_agent import memo_is_usable

    assert not memo_is_usable("Sorry, something went wrong. Please try your request again.")
    assert not memo_is_usable("")
    assert memo_is_usable("# Memo\n" + "Substantive paragraph about the transaction. " * 20)


def test_off_topic_chat_reply_is_not_a_memo_and_is_retried():
    import asyncio
    from types import SimpleNamespace

    from app.agents.investment_memo_agent import InvestmentMemoAgent, memo_is_usable

    assert not memo_is_usable("Sure. First, I'll need your permission to turn on the Adobe app.")
    assert not memo_is_usable("I can help with that, but first tell me more.")
    assert memo_is_usable("# Executive Summary\nDraft prose.")
    assert memo_is_usable("memo")
    assert memo_is_usable("Revenue grew 12.7% to $457.5M.")

    replies = iter([{"content": "Sure. First, I'll need your permission to turn on the Adobe app."},
                    {"content": "# Investment Memo\nSprout Social revenue was $457.5M."}])
    agent = InvestmentMemoAgent.__new__(InvestmentMemoAgent)
    agent.name = "investment_memo_agent"
    agent.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)

    async def fake(prompt, system_prompt=None):
        return next(replies)

    agent.generate_with_tools = fake
    out = asyncio.run(agent.run("Draft IC memo", {"deal_id": "d-retry"}))
    assert out.success and "457.5M" in out.data["memo"]
