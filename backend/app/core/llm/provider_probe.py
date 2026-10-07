"""Bounded, content-validating probes for configured language model providers."""

import asyncio
import time
from typing import Any, Dict, Optional


def _safe_failure(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "The model did not respond before the test timeout."

    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or status
    if status == 401:
        return "The provider rejected these credentials (HTTP 401)."
    if status == 403:
        return "The provider denied this request; check access, project, and billing (HTTP 403)."
    if status == 404:
        return "The selected model or provider endpoint was not found (HTTP 404)."
    if status == 429:
        return "The provider rate-limited this test (HTTP 429)."
    return f"The provider test failed ({type(exc).__name__})."


def _client(provider: str, model: str, api_key: str, base_url: Optional[str] = None):
    from app.core.llm.gemini_client import GeminiClient, MistralClient, OpenAIClient
    from app.core.llm.nvidia_client import NvidiaClient
    from app.core.llm.vertex_client import VertexClient
    from app.core.llm.openrouter_client import OpenRouterClient
    from app.core.llm.claude_client import ClaudeClient
    from app.core.llm.groq_client import GroqClient

    if provider == "gemini":
        return GeminiClient(model=model, api_key=api_key)
    if provider == "openai":
        return OpenAIClient(model=model, api_key=api_key)
    if provider == "openrouter":
        return OpenRouterClient(model=model, api_key=api_key)
    if provider == "claude":
        return ClaudeClient(api_key=api_key, model=model)
    if provider == "groq":
        return GroqClient(api_key=api_key, model=model)
    if provider == "mistral":
        return MistralClient(model=model, api_key=api_key)
    if provider == "vertex":
        return VertexClient(model=model, api_key=api_key)
    if provider == "nvidia":
        return NvidiaClient(model=model, base_url=base_url, api_key=api_key)
    raise ValueError("Unsupported provider")


async def probe_provider(
    provider: str,
    model: str,
    api_key: str,
    *,
    base_url: Optional[str] = None,
    timeout_seconds: float = 20,
) -> Dict[str, Any]:
    """Perform one small real generation; never return prompt, completion, or key."""
    started = time.monotonic()
    try:
        client = _client(provider, model, api_key, base_url)
        max_probe_tokens = 128 if provider == "openrouter" else 12
        result = await asyncio.wait_for(
            client.generate(
                prompt="Reply with the single word OK.",
                system_prompt="This is a connectivity check. Do not include private or external data.",
                temperature=0,
                max_tokens=max_probe_tokens,
            ),
            timeout=timeout_seconds,
        )
        content = result.get("content") if isinstance(result, dict) else None
        tool_calls = result.get("function_calls") if isinstance(result, dict) else None
        if not (isinstance(content, str) and content.strip()) and not tool_calls:
            return {
                "ok": False,
                "status": "empty_response",
                "error": "The provider responded but returned no text or tool call.",
                "model": model,
                "latency_ms": round((time.monotonic() - started) * 1000),
            }
        return {
            "ok": True,
            "status": "ready",
            "model": model,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "output_characters": len(content.strip()) if isinstance(content, str) else 0,
            "tool_calls": len(tool_calls or []),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "error": _safe_failure(exc),
            "model": model,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
