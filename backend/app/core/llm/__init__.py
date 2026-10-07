"""LLM clients module"""

from app.core.llm.gemini_client import GeminiClient, OpenAIClient, MistralClient
from app.core.llm.local_llm_client import OllamaClient, LMStudioClient
from app.core.llm.nvidia_client import NvidiaClient
from app.core.llm.vertex_client import VertexClient
from app.core.llm.claude_client import ClaudeClient
from app.core.llm.groq_client import GroqClient
from app.core.llm.openrouter_client import OpenRouterClient
from app.config import get_settings
import structlog

logger = structlog.get_logger()


def get_llm_client(provider=None):
    """Factory to get a client using the live Settings UI credentials and model choices."""
    settings = get_settings()
    provider = provider or getattr(settings, "DEFAULT_LLM_PROVIDER", "gemini").lower()

    try:
        from app.core.settings_service import SettingsService

        runtime = SettingsService.get_instance()
    except Exception:
        runtime = None

    def configured(key, env_attr, fallback=None):
        value = runtime.get(key) if runtime else None
        if isinstance(value, str) and value.strip() and value.strip() not in {"***", "placeholder_key"}:
            return value.strip()
        return getattr(settings, env_attr, fallback)

    if provider == "openai":
        return OpenAIClient(
            api_key=configured("openai_api_key", "OPENAI_API_KEY"),
            model=configured("openai_model", "OPENAI_MODEL"),
        )
    elif provider == "openrouter":
        return OpenRouterClient(
            api_key=configured("openrouter_api_key", "OPENROUTER_API_KEY"),
            model=configured("openrouter_model", "OPENROUTER_MODEL"),
        )
    elif provider == "mistral":
        return MistralClient(
            api_key=configured("mistral_api_key", "MISTRAL_API_KEY"),
            model=configured("mistral_model", "MISTRAL_MODEL"),
        )
    elif provider == "ollama":
        return OllamaClient(
            model=configured("ollama_model", "OLLAMA_MODEL"),
            base_url=configured("ollama_base_url", "OLLAMA_BASE_URL"),
        )
    elif provider == "lmstudio":
        return LMStudioClient(
            model=configured("lmstudio_model", "LMSTUDIO_MODEL"),
            base_url=configured("lmstudio_base_url", "LMSTUDIO_BASE_URL"),
        )
    elif provider == "gemini":
        return GeminiClient(
            model=configured("gemini_model", "GEMINI_MODEL"),
            api_key=configured("gemini_api_key", "GEMINI_API_KEY"),
        )
    elif provider == "nvidia":
        return NvidiaClient(
            model=configured("nvidia_model", "NVIDIA_MODEL"),
            base_url=configured("nvidia_base_url", "NVIDIA_BASE_URL"),
            api_key=configured("nvidia_api_key", "NVIDIA_API_KEY"),
        )
    elif provider == "vertex":
        return VertexClient(
            model=configured("vertex_model", "VERTEX_MODEL"),
            api_key=configured("vertex_api_key", "VERTEX_API_KEY"),
        )
    elif provider == "claude":
        return ClaudeClient(
            configured("anthropic_api_key", "ANTHROPIC_API_KEY"),
            configured("anthropic_model", "ANTHROPIC_MODEL"),
        )
    elif provider == "groq":
        return GroqClient(
            configured("groq_api_key", "GROQ_API_KEY"),
            configured("groq_model", "GROQ_MODEL"),
        )
    else:
        logger.warning(
            f"Unknown LLM provider '{provider}', falling back to Gemini",
            provider=provider,
        )
        return GeminiClient()


__all__ = [
    "GeminiClient",
    "OpenAIClient",
    "MistralClient",
    "OllamaClient",
    "LMStudioClient",
    "NvidiaClient",
    "VertexClient",
    "ClaudeClient",
    "GroqClient",
    "OpenRouterClient",
    "get_llm_client",
]
