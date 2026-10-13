import asyncio
from types import SimpleNamespace

import pytest

from app.core.llm.gemini_client import OpenAIClient, looks_like_chat_failure


@pytest.mark.parametrize("text", [
    "Sorry, something went wrong. Please try your request again.",
    "I encountered an error doing what you asked. Could you try again?",
    "I'm sorry, I can't help with that.",
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
