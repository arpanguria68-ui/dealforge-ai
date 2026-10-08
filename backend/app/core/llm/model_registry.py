"""
Model Capability Registry — Static knowledge of LLM capabilities.
Used by LLMGateway to auto-adapt requests (tool format, JSON mode, context limits).
"""
import dataclasses
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelCapabilities:
    """Capabilities of a specific LLM model."""
    tool_calling: bool = False           # Native function/tool calling support
    json_mode: bool = False              # Reliable JSON output mode
    thinking: bool = False               # Explicit chain-of-thought tokens
    think_tag: Optional[str] = None      # Tag used: "<think>", "<thinking>", etc.
    vision: bool = False                 # Multimodal image input
    context_window: int = 8_192          # Max input + output tokens
    max_output_tokens: int = 4_096       # Max generation length
    supports_system_prompt: bool = True  # Some models (o1) don't support system
    tier: str = "general"                # "fast", "general", "reasoning", "embedding"
    streaming: bool = True               # Supports streaming output


# ── Ollama local models ─────────────────────────────────────────────────────
OLLAMA_REGISTRY: dict[str, ModelCapabilities] = {
    "llama3":             ModelCapabilities(tool_calling=True,  json_mode=True, context_window=8_192,   tier="general"),
    "llama3.1":           ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="general"),
    "llama3.1:8b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="fast"),
    "llama3.1:70b":       ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="general"),
    "llama3.2":           ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="fast"),
    "llama3.2:3b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="fast"),
    "qwen2.5":            ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5:7b":         ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="fast"),
    "qwen2.5:14b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5:32b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5:72b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5-coder":      ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5-coder:32b":  ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "deepseek-r1":        ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:7b":     ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:8b":     ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:14b":    ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:32b":    ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:70b":    ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-v3":        ModelCapabilities(tool_calling=True,  json_mode=True,  thinking=False, context_window=65_536, tier="general"),
    "qwq":                ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=32_768,  tier="reasoning"),
    "qwq:32b":            ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=32_768,  tier="reasoning"),
    "phi4":               ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=16_384, tier="general"),
    "phi4:14b":           ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=16_384, tier="general"),
    "mistral":            ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="general"),
    "mistral:7b":         ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="fast"),
    "mixtral":            ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="general"),
    "mixtral:8x7b":       ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="general"),
    "gemma2":             ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="fast"),
    "gemma2:9b":          ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="fast"),
    "gemma2:27b":         ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "gemma3":             ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000,tier="general"),
    "gemma3:27b":         ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000,tier="general"),
    "phi3":               ModelCapabilities(tool_calling=False, json_mode=True,  context_window=4_096,  tier="fast"),
    "phi3.5":             ModelCapabilities(tool_calling=False, json_mode=True,  context_window=128_000,tier="fast"),
    "llava":              ModelCapabilities(tool_calling=False, json_mode=False, vision=True,  context_window=4_096,  tier="fast"),
    "llava:13b":          ModelCapabilities(tool_calling=False, json_mode=False, vision=True,  context_window=4_096,  tier="general"),
    "nomic-embed-text":   ModelCapabilities(tool_calling=False, json_mode=False, context_window=8_192,  tier="embedding"),
}

# ── Cloud models ─────────────────────────────────────────────────────────────
CLOUD_REGISTRY: dict[str, ModelCapabilities] = {
    # Google Gemini
    "gemini-3.8-flash":           ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="reasoning"),
    "gemini-3.7-flash":           ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="general"),
    "gemini-3.6-flash":           ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="general"),
    "gemini-3.5-flash":           ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="general"),
    "gemini-3.5-flash-lite":      ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="fast"),
    "gemini-3.1-flash-lite":      ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="fast"),
    "gemini-3.1-pro-preview":     ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="reasoning"),
    "gemini-2.5-flash":           ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="general"),
    "gemini-2.5-flash-lite":      ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="fast"),
    "gemini-2.5-pro":             ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="reasoning"),
    "z-ai/glm-5.3":               ModelCapabilities(tool_calling=True, json_mode=True, thinking=True, context_window=1_048_576, max_output_tokens=65_536, tier="reasoning"),
    # OpenRouter's selected free Nemotron model. Keep the documented 256K
    # context while avoiding assumptions about JSON mode or tool calling.
    "nemotron-3-super-120b-a12b:free": ModelCapabilities(context_window=262_144, max_output_tokens=8_192, tier="reasoning"),

    # OpenAI
    "gpt-4o":                     ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000, max_output_tokens=16_384, tier="general"),
    "gpt-4o-mini":                ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000, max_output_tokens=16_384, tier="fast"),
    "o1":                         ModelCapabilities(tool_calling=False, json_mode=False, thinking=True, supports_system_prompt=False, context_window=200_000, tier="reasoning"),
    "o1-mini":                    ModelCapabilities(tool_calling=False, json_mode=False, thinking=True, supports_system_prompt=False, context_window=128_000, tier="reasoning"),
    "o3":                         ModelCapabilities(tool_calling=True,  json_mode=True,  thinking=True, context_window=200_000, max_output_tokens=100_000, tier="reasoning"),
    "o3-mini":                    ModelCapabilities(tool_calling=True,  json_mode=True,  thinking=True, context_window=200_000, tier="reasoning"),
    "gpt-4-turbo":                ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000, tier="general"),

    # Anthropic Claude
    "claude-3-5-sonnet":          ModelCapabilities(tool_calling=True,  json_mode=False, context_window=200_000, max_output_tokens=8_192, tier="general"),
    "claude-3-5-haiku":           ModelCapabilities(tool_calling=True,  json_mode=False, context_window=200_000, max_output_tokens=8_192, tier="fast"),
    "claude-3-7-sonnet":          ModelCapabilities(tool_calling=True,  json_mode=False, thinking=True, think_tag="<thinking>", context_window=200_000, max_output_tokens=16_000, tier="reasoning"),
    "claude-opus-4":              ModelCapabilities(tool_calling=True,  json_mode=False, thinking=True, context_window=200_000, max_output_tokens=32_000, tier="reasoning"),

    # Mistral
    "mistral-large":              ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, max_output_tokens=8_192, tier="general"),
    "mistral-medium":             ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, max_output_tokens=8_192, tier="general"),
    "mistral-small":              ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, max_output_tokens=8_192, tier="fast"),
}

# ── LM Studio local models ─────────────────────────────────────────────────
# LM Studio's OpenAI-compatible server honors response_format=json_object
# structurally, so json_mode=True (the client maps it). Tool calling is
# model-dependent and usually absent → False so the gateway warns and agents
# use the ReAct text fallback. Context windows are conservative on purpose:
# small local models degrade past ~8k even when they advertise more.
LMSTUDIO_REGISTRY: dict[str, ModelCapabilities] = {
    "local-model":        ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "llama3.1":           ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "qwen2.5":            ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "qwen2.5-coder":      ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "mistral-7b":         ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="fast"),
    "phi-4":              ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "gemma-3":            ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "deepseek-r1-distill": ModelCapabilities(tool_calling=False, json_mode=True, thinking=True, think_tag="<think>", context_window=8_192, tier="reasoning"),
}

# ── Combined Registry ────────────────────────────────────────────────────────
MODEL_REGISTRY: dict[str, ModelCapabilities] = {**OLLAMA_REGISTRY, **LMSTUDIO_REGISTRY, **CLOUD_REGISTRY}

# Context windows observed at runtime (e.g. LM Studio's loaded context length),
# keyed by (provider, model). These override the static table, because local
# windows depend on how the model was loaded, not on the model family.
_LIVE_CONTEXT: dict = {}


def set_live_context_window(provider: str, model: str, tokens: Optional[int]) -> None:
    if tokens and int(tokens) > 0:
        _LIVE_CONTEXT[((provider or "").lower(), model or "")] = int(tokens)


def ollama_max_ctx() -> int:
    """Largest num_ctx DealForge requests from Ollama (VRAM grows with it)."""
    try:
        return max(2048, int(os.getenv("OLLAMA_MAX_CTX", "16384")))
    except ValueError:
        return 16384


def get_capabilities(model: str, provider: str = "") -> ModelCapabilities:
    """Static capabilities adjusted to the effective runtime context window.

    - A live window discovered from the provider wins.
    - Ollama runs with the ``num_ctx`` the client requests (capped by
      OLLAMA_MAX_CTX), not the family's maximum, so the guard must budget to
      that cap or prompts are silently cut by Ollama from the front.
    """
    caps = _static_capabilities(model, provider)
    p = (provider or "").lower()
    # "" = the model the server has loaded when no name was configured.
    live = _LIVE_CONTEXT.get((p, model or "")) or _LIVE_CONTEXT.get((p, ""))
    if live:
        return dataclasses.replace(caps, context_window=live)
    if p == "ollama":
        return dataclasses.replace(caps, context_window=min(caps.context_window, ollama_max_ctx()))
    return caps


def _static_capabilities(model: str, provider: str = "") -> ModelCapabilities:
    """Look up model capabilities. Returns safe defaults if unknown.

    Provider-aware: the same model id (e.g. "llama3.1") has different
    effective capabilities under Ollama vs LM Studio (native tools/JSON vs
    server-side structural JSON, context discipline), so the provider's own
    table wins on exact match.
    """
    provider = (provider or "").lower()
    if provider in ("lmstudio", "lm-studio"):
        caps = LMSTUDIO_REGISTRY.get(model)
        if caps:
            return caps
    if provider == "ollama":
        caps = OLLAMA_REGISTRY.get(model)
        if caps:
            return caps
    if provider == "openrouter" and "/" in model:
        # Reuse known capabilities for namespaced upstream model slugs, but do
        # not infer capabilities for models absent from the registry.
        upstream_model = model.split("/", 1)[1]
        caps = MODEL_REGISTRY.get(upstream_model)
        if caps:
            return caps
    caps = MODEL_REGISTRY.get(model)
    if caps:
        return caps
    
    # Try normalized model name for Ollama patterns (e.g., 'llama3.1:70b' -> 'llama3.1')
    if ":" in model:
        base_model = model.split(":")[0]
        if base_model in MODEL_REGISTRY:
            return MODEL_REGISTRY[base_model]

    # Provider-based fallbacks
    if provider in ("lmstudio", "lm-studio"):
        return ModelCapabilities(tool_calling=False, json_mode=True, context_window=8_192, tier="general")
    if provider == "ollama":
        return ModelCapabilities(tool_calling=True, json_mode=True, context_window=8_192, tier="general")
    if provider in ("gemini", "vertex"):
        return ModelCapabilities(tool_calling=True, json_mode=True, context_window=1_048_576, tier="general")
    if provider == "openai":
        return ModelCapabilities(tool_calling=True, json_mode=True, context_window=128_000, tier="general")
    if provider == "openrouter":
        # OpenRouter spans many vendors; unknown model capabilities are not uniform.
        return ModelCapabilities(tool_calling=False, json_mode=False, context_window=32_768, tier="general")
    if provider in ("claude", "anthropic"):
        return ModelCapabilities(tool_calling=True, json_mode=False, context_window=200_000, tier="general")
    if provider == "mistral":
        return ModelCapabilities(tool_calling=True, json_mode=True, context_window=32_768, tier="general")
    
    return ModelCapabilities()  # Safe defaults
