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

def enforce_context_limit(
    prompt: str,
    system_prompt: Optional[str] = None,
    provider: str = "gemini",
    model: str = "",
    context_window: Optional[int] = None,
) -> tuple[str, Optional[str], bool]:
    """
    Ensures prompt + system_prompt fits within model's context window.
    Truncates prompt if necessary, preserving system_prompt.
    """
    # 1. Determine context window
    if not context_window:
        # Fallback to registry or provider defaults
        caps = get_capabilities(model, provider)
        context_window = caps.context_window

    # Safety buffer (keep 15% for generation)
    limit = int(context_window * 0.85)

    sys_tokens = estimate_tokens(system_prompt or "")
    prompt_tokens = estimate_tokens(prompt)
    total_tokens = sys_tokens + prompt_tokens

    if total_tokens <= limit:
        return prompt, system_prompt, False

    # Calculate how many tokens we need to trim
    excess_tokens = total_tokens - limit
    excess_chars = int(excess_tokens * CHARS_PER_TOKEN)

    logger.warning(
        "context_limit_exceeded",
        provider=provider,
        estimated_tokens=total_tokens,
        limit=limit,
        excess_tokens=excess_tokens,
    )

    # Middle-truncation on prompt (preserve first 60% + last 20%)
    if len(prompt) > excess_chars:
        keep_start = int(len(prompt) * 0.60)
        keep_end   = int(len(prompt) * 0.20)
        truncation_notice = "\n\n[...CONTEXT TRUNCATED TO FIT MODEL CONTEXT WINDOW...]\n\n"
        prompt = prompt[:keep_start] + truncation_notice + prompt[-keep_end:]

    return prompt, system_prompt, True
