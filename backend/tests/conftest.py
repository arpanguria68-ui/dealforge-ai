import pytest


@pytest.fixture(autouse=True)
def _no_live_tool_llm(monkeypatch):
    """Tool text extraction never reaches a real LLM in tests (keyword fallback).

    Tests that exercise extraction patch ``grounded_extraction.llm_call``.
    """
    from app.core.tools import grounded_extraction

    async def _unavailable(task, prompt, system_prompt):
        return None

    monkeypatch.setattr(grounded_extraction, "llm_call", _unavailable)


@pytest.fixture(autouse=True)
def _no_laya_warmup(monkeypatch):
    """App lifespan must not start loading Laya checkpoints during tests."""
    monkeypatch.setenv("LAYA_WARMUP", "false")
