"""DealForge AI Configuration"""

from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    """Application settings"""

    # App
    APP_NAME: str = "DealForge AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    DATA_DIR: str = "data"
    REPORTS_DIR: str = "reports"

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/dealforge"
    )
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    ADMIN_API_TOKEN: Optional[str] = None
    REQUIRE_ADMIN_TOKEN: bool = False
    LLM_STARTUP_PROBE: bool = False
    CORS_ORIGINS: Optional[str] = None

    # OpenAI / Codex
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o"
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_MODEL: str = "openai/gpt-4o-mini"
    CODEX_MODEL: str = "gpt-5.1-codex-max"

    # Mistral
    MISTRAL_API_KEY: Optional[str] = None
    MISTRAL_MODEL: str = "mistral-large-latest"

    # Gemini
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.8-flash"

    # Vertex AI
    VERTEX_API_KEY: Optional[str] = None
    VERTEX_PROJECT_ID: Optional[str] = None
    VERTEX_LOCATION: str = "us-central1"
    VERTEX_MODEL: str = "gemini-3.8-flash"

    # NVIDIA
    NVIDIA_API_KEY: Optional[str] = None
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_MODEL: str = "z-ai/glm-5.3"

    # Anthropic Claude (used by ClaudeClient via ANTHROPIC_API_KEY)
    ANTHROPIC_API_KEY: Optional[str] = None
    CLAUDE_MODEL: str = "claude-3-5-sonnet"

    # Groq (used by GroqClient via GROQ_API_KEY)
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.1-70b-versatile"

    # Local LLMs
    DEFAULT_LLM_PROVIDER: str = (
        "gemini"  # gemini, vertex, openai, mistral, ollama, lmstudio
    )
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"
    LMSTUDIO_BASE_URL: str = "http://localhost:1234/v1"
    LMSTUDIO_MODEL: str = "local-model"
    LMSTUDIO_REASONING: str = "off"

    # PageIndex
    PAGEINDEX_API_KEY: Optional[str] = None
    PAGEINDEX_BASE_URL: str = "https://api.vectify.ai/v1"
    PAGEINDEX_MODE: str = "local"  # "local" (self-hosted) or "cloud" (VectifyAI API)
    PAGEINDEX_STORAGE_DIR: Optional[str] = None  # Local storage path for indexes

    # Financial Data
    FMP_API_KEY: Optional[str] = None
    FINANCIAL_DATASETS_API_KEY: Optional[str] = None
    ALPHA_VANTAGE_API_KEY: Optional[str] = None
    FINNHUB_API_KEY: Optional[str] = None
    SEC_API_KEY: Optional[str] = None  # Optional — for sec-api.io paid tier

    # Agent Settings
    MAX_AGENT_ITERATIONS: int = 10
    AGENT_TIMEOUT_SECONDS: int = 180
    AGENT_MODEL_MAP: Optional[str] = None

    # Deal Scoring
    DEAL_SCORING_THRESHOLD: float = 0.65

    # Memory & Context (New PRD Requirements)
    MEMORY_STALENESS_DAYS: int = 30
    DEAL_ISOLATION_MODE: bool = True
    SEARCH_CACHE_TTL: int = 86400  # 24 hours
    MAX_CLARIFICATION_DEPTH: int = 3

    # OfficeCLI (Document Automation)
    OFFICECLI_PATH: Optional[str] = None  # Path to officecli binary
    OFFICECLI_AUTO_DOWNLOAD: bool = True  # Auto-download if not found

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
