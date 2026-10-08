"""
Context window guard — truncates prompts that exceed model limits.
Uses middle-truncation: preserves start (instructions) + end (question/task).
"""
import structlog

logger = structlog.get_logger()

# Conservative chars-per-token ratio (English financial text)
CHARS_PER_TOKEN = 3.5

# Provider → default context window (tokens) if model unknown
PROVIDER_DEFAULTS = {
    "ollama":    8_192,
    "lmstudio":  12_288,
    "gemini":    1_048_576,
    "openai":    128_000,
    "openrouter": 32_768,
    "mistral":   32_768,
    "nvidia":    16_384,
    "vertex":    1_048_576,
}


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


from typing import Optional
from app.core.llm.model_registry import get_capabilities

def _middle_truncate(text: str, keep_chars: int, notice: str) -> str:
    """Keep the head (instructions/data) and tail (latest task/results)."""
    if len(text) <= keep_chars:
        return text
    keep_chars = max(0, keep_chars - len(notice))
    head = int(keep_chars * 0.75)
    tail = keep_chars - head
    return text[:head] + notice + (text[-tail:] if tail > 0 else "")


def enforce_context_limit(
    prompt: str,
    system_prompt: Optional[str] = None,
    provider: str = "gemini",
    model: str = "",
    context_window: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    extra_tokens: int = 0,
) -> tuple[str, Optional[str], bool]:
    """Fit system prompt + prompt (+ tool schemas) into the model's window.

    Reserves the larger of 15% of the window or the requested
    ``max_output_tokens`` for generation, plus ``extra_tokens`` (e.g. tool
    schemas). Previously the cut always kept 80% of the prompt whatever the
    overflow, ignored ``max_tokens`` and tool schemas, and never shortened an
    oversized system prompt, so large requests were still rejected upstream.
    """
    if not context_window:
        caps = get_capabilities(model, provider)
        context_window = caps.context_window

    reserve = max(int(context_window * 0.15), int(max_output_tokens or 0))
    limit = max(256, context_window - reserve - int(extra_tokens or 0))

    sys_tokens = estimate_tokens(system_prompt or "")
    prompt_tokens = estimate_tokens(prompt)
    total_tokens = sys_tokens + prompt_tokens
    if total_tokens <= limit:
        return prompt, system_prompt, False

    logger.warning(
        "context_limit_exceeded",
        provider=provider,
        estimated_tokens=total_tokens,
        limit=limit,
        excess_tokens=total_tokens - limit,
    )

    # The system prompt may use at most 40% of the budget (it carries role,
    # skill/sector/knowledge-graph blocks and ReAct tool lists).
    if system_prompt and sys_tokens > int(limit * 0.4):
        system_prompt = _middle_truncate(
            system_prompt, int(limit * 0.4 * CHARS_PER_TOKEN),
            "\n\n[...SYSTEM CONTEXT TRUNCATED TO FIT MODEL CONTEXT WINDOW...]\n\n",
        )
        sys_tokens = estimate_tokens(system_prompt)

    prompt_budget_chars = int(max(64, limit - sys_tokens) * CHARS_PER_TOKEN)
    prompt = _middle_truncate(
        prompt, prompt_budget_chars,
        "\n\n[...CONTEXT TRUNCATED TO FIT MODEL CONTEXT WINDOW...]\n\n",
    )
    return prompt, system_prompt, True
