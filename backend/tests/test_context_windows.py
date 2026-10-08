"""Context-window handling: guard, routing estimates, adaptive budgets, local runtimes."""

import pytest

from app.core.llm import model_registry
from app.core.llm.context_guard import enforce_context_limit, estimate_tokens


@pytest.fixture(autouse=True)
def _clear_live_context():
    model_registry._LIVE_CONTEXT.clear()
    yield
    model_registry._LIVE_CONTEXT.clear()


def test_guard_cuts_proportionally_so_large_overflows_fit():
    window = 8192
    prompt = "HEAD-INSTRUCTIONS " + ("data " * 20_000) + " LATEST-RESULTS"  # ~100K chars, ~28K tokens
    new_prompt, new_sys, truncated = enforce_context_limit(
        prompt, "You are an analyst.", "lmstudio", "local-model", context_window=window, max_output_tokens=2048,
    )
    assert truncated
    total = estimate_tokens(new_prompt) + estimate_tokens(new_sys)
    assert total <= window - 2048, "old guard kept 80% of the prompt whatever the overflow"
    assert new_prompt.startswith("HEAD-INSTRUCTIONS") and new_prompt.endswith("LATEST-RESULTS")


def test_guard_reserves_output_and_tool_tokens_and_trims_huge_system_prompts():
    window = 8192
    system = "ROLE. " + ("skill " * 10_000) + " REACT TOOL LIST"
    prompt = "Analyse Acme."
    _, new_sys, truncated = enforce_context_limit(
        prompt, system, "ollama", "llama3", context_window=window, max_output_tokens=4096, extra_tokens=1000,
    )
    assert truncated
    assert estimate_tokens(new_sys) <= int((window - 4096 - 1000) * 0.4) + 50
    assert new_sys.startswith("ROLE.") and new_sys.endswith("REACT TOOL LIST")
    # Fits untouched when small.
    assert enforce_context_limit("hi", "sys", "gemini", "", context_window=1_000_000)[2] is False


def test_ollama_effective_window_is_capped_and_num_ctx_sized_to_request(monkeypatch):
    from app.core.llm.local_llm_client import _ollama_num_ctx

    monkeypatch.setenv("OLLAMA_MAX_CTX", "16384")
    assert model_registry.get_capabilities("llama3.1", "ollama").context_window == 16384
    small = _ollama_num_ctx([{"content": "hi"}], None, 512)
    big = _ollama_num_ctx([{"content": "x" * 40_000}], None, 2048)
    huge = _ollama_num_ctx([{"content": "x" * 400_000}], None, 2048)
    assert small == 4096 and big == 16384 and huge == 16384


def test_live_lmstudio_context_overrides_static_registry():
    from app.core.llm.model_router import ModelRouter

    payload = {"models": [
        {"key": "other", "loaded_instances": []},
        {"key": "qwen/qwen3-8b", "loaded_instances": [{"id": "i", "config": {"context_length": 4096}}]},
    ]}
    tokens = ModelRouter._lmstudio_context_from_models(payload, "qwen/qwen3-8b")
    assert tokens == 4096
    model_registry.set_live_context_window("lmstudio", "qwen/qwen3-8b", tokens)
    assert model_registry.get_capabilities("qwen/qwen3-8b", "lmstudio").context_window == 4096
    # v0 API shape
    v0 = {"data": [{"id": "m", "state": "loaded", "max_context_length": 32768, "loaded_context_length": 16384}]}
    assert ModelRouter._lmstudio_context_from_models(v0, "m") == 16384


@pytest.mark.asyncio
async def test_router_uses_real_request_size_for_context_fit(monkeypatch):
    from app.core.llm.model_router import ModelRouter
    from app.core.laya import client as laya_client

    class _NoTier:
        async def route_tier(self, _text):
            return None

    monkeypatch.setattr(laya_client, "get_laya_client", lambda: _NoTier())
    router = ModelRouter()
    seen = {}

    async def fake_fallback(agent, est_tokens=500):
        seen["est"] = est_tokens
        return "gemini", False

    monkeypatch.setattr(router, "get_provider_with_fallback", fake_fallback)
    await router.get_model_route_for_text("financial_analyst", "short excerpt", est_tokens=50_000)
    assert seen["est"] == 50_000


def test_tool_result_budget_adapts_to_model_window(monkeypatch):
    from app.agents.base import BaseAgent

    model_registry.set_live_context_window("lmstudio", "tiny", 4096)
    small = BaseAgent._tool_context_budget("lmstudio", "tiny", request_tokens=3000)
    large = BaseAgent._tool_context_budget("gemini", "gemini-2.0-flash", request_tokens=1500)
    assert small == BaseAgent._TOOL_CONTEXT_MIN_CHARS
    assert large == BaseAgent._TOOL_CONTEXT_CHAR_LIMIT
    mid = BaseAgent._tool_context_budget("lmstudio", "tiny", request_tokens=0)
    assert BaseAgent._TOOL_CONTEXT_MIN_CHARS < mid < BaseAgent._TOOL_CONTEXT_CHAR_LIMIT

    results = [{"name": "web_search", "data": "x" * 20_000}] * 3
    rendered = BaseAgent._format_tool_results(results, 3000)
    assert len(rendered) <= 3100
