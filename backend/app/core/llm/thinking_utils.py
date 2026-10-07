"""Utility functions for handling thinking/reasoning model outputs."""


def strip_thinking_tokens(text: str, tag: str = "<think>") -> str:
    """
    Remove <think>...</think> blocks (and variants) from model output.
    Handles nested blocks and multiple occurrences.
    Supported tags: <think>, <thinking>, <reasoning>
    """
    if not text:
        return text
    close_tag = tag.replace("<", "</")
    result = text
    max_iter = 20  # safety cap for pathological inputs
    iterations = 0
    while tag in result and close_tag in result and iterations < max_iter:
        start = result.find(tag)
        end = result.find(close_tag)
        if end == -1:
            break
        end += len(close_tag)
        result = result[:start] + result[end:]
        iterations += 1
    return result.strip()


def extract_thinking(text: str, tag: str = "<think>") -> tuple:
    """
    Returns (clean_output, thinking_content) as separate strings.
    Use this when you want to log/store the reasoning separately.
    """
    if not text:
        return text, ""
    close_tag = tag.replace("<", "</")
    thinking_parts = []
    result = text
    max_iter = 20
    iterations = 0
    while tag in result and close_tag in result and iterations < max_iter:
        start = result.find(tag)
        end_tag_start = result.find(close_tag)
        if end_tag_start == -1:
            break
        end = end_tag_start + len(close_tag)
        thinking_parts.append(result[start + len(tag):end_tag_start])
        result = result[:start] + result[end:]
        iterations += 1
    return result.strip(), "\n".join(thinking_parts)


def detect_thinking_tag(text: str):
    """Auto-detect which thinking tag style a model uses."""
    for tag in ["<think>", "<thinking>", "<reasoning>"]:
        if tag in text:
            return tag
    return None


def has_thinking_tokens(text: str) -> bool:
    return detect_thinking_tag(text) is not None
