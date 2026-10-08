"""
Smart Model Router — Local-First with Cloud Fallback

Strategy:
1. Try local LLM (Ollama/LM Studio) first for ALL agents
2. If local fails (timeout, not running), fallback to cloud (Gemini/OpenAI)
3. Complex reasoning agents can be forced to cloud via routing table
4. Logs which provider was actually used for transparency

Mimics how McKinsey staffs: try junior analyst first, escalate to partner if needed.
"""

import os
import time
from typing import Dict, Optional, Tuple
import structlog
import httpx
from app.core.settings_service import SettingsService

logger = structlog.get_logger()

# Health-cache TTL: the old code hit /api/tags or /v1/models (≈2s timeout)
# on EVERY agent call. Cache per provider to avoid serial latency pile-up.
_LOCAL_HEALTH_TTL_SECONDS = float(os.environ.get("ROUTER_HEALTH_TTL", "60") or 60)


# Default routing table: agent_name → preferred provider
DEFAULT_AGENT_ROUTING = {
    # ===== Complex reasoning → Local LLM (Performance Eval) =====
    "financial_analyst": "lmstudio",
    "valuation_agent": "lmstudio",
    "legal_advisor": "lmstudio",
    "risk_assessor": "lmstudio",
    "debate_moderator": "lmstudio",
    "due_diligence_agent": "lmstudio",
    "investment_memo_agent": "lmstudio",
    "compliance_qa_agent": "lmstudio",
    "dcf_lbo_architect": "lmstudio",
    "ofas_supervisor": "lmstudio",
    "complex_reasoning": "gemini",  # Forced to cloud for complex Chain-of-Thought
    "advanced_financial_modeler": "gemini",  # High logic load
    "data_curator": "lmstudio",
    "report_architect": "lmstudio",
    # ===== Lighter tasks → Local LLM =====
    "market_researcher": "lmstudio",
    "market_risk_agent": "lmstudio",
    "compliance_agent": "lmstudio",
    "scoring_agent": "lmstudio",
}


# Task-type routing for ad-hoc requests
TASK_TYPE_ROUTING = {
    "analysis": "gemini",
    "reasoning": "gemini",
    "valuation": "gemini",
    "legal": "gemini",
    "debate": "gemini",
    "extraction": "ollama",
    "formatting": "ollama",
    "summarization": "ollama",
    "classification": "ollama",
}

# Cloud providers that can serve as fallback
CLOUD_PROVIDERS = {"gemini", "vertex", "openai", "openrouter", "mistral", "nvidia", "claude", "groq"}
LOCAL_PROVIDERS = {"ollama", "lmstudio"}


_POOL_SETTING_ATTRS = {
    "LAYA_FAST_POOL": "fast_pool",
    "LAYA_GENERAL_POOL": "general_pool",
    "LAYA_REASONING_POOL": "reasoning_pool",
}


def _env_pool(name: str, default: list) -> list:
    """Comma-separated pool order: env override → Settings store → default."""
    raw = os.environ.get(name, "")
    if not raw.strip():
        try:
            from app.core.settings_service import SettingsService

            laya = SettingsService.get_instance().get("laya", {}) or {}
            stored = laya.get(_POOL_SETTING_ATTRS.get(name, ""), "")
            raw = str(stored or "")
        except Exception:
            raw = ""
    if raw.strip():
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return list(default)


# Laya tier → provider pools (ordered by cost/latency preference).
# Env-overridable: LAYA_FAST_POOL, LAYA_GENERAL_POOL, LAYA_REASONING_POOL.
FAST_POOL = _env_pool("LAYA_FAST_POOL", ["groq", "gemini", "mistral"])
GENERAL_POOL = _env_pool("LAYA_GENERAL_POOL", ["gemini", "openrouter", "nvidia", "openai", "mistral", "vertex"])
REASONING_POOL = _env_pool("LAYA_REASONING_POOL", ["vertex", "openrouter", "claude", "openai", "gemini"])

_PROVIDER_MODEL_SETTINGS = {
    "gemini": ("gemini_model", "GEMINI_MODEL"),
    "openai": ("openai_model", "OPENAI_MODEL"),
    "openrouter": ("openrouter_model", "OPENROUTER_MODEL"),
    "mistral": ("mistral_model", "MISTRAL_MODEL"),
    "ollama": ("ollama_model", "OLLAMA_MODEL"),
    "lmstudio": ("lmstudio_model", "LMSTUDIO_MODEL"),
    "nvidia": ("nvidia_model", "NVIDIA_MODEL"),
    "vertex": ("vertex_model", "VERTEX_MODEL"),
    "claude": ("anthropic_model", "ANTHROPIC_MODEL"),
    "groq": ("groq_model", "GROQ_MODEL"),
}


def get_configured_model(provider: str, settings=None) -> str:
    """Resolve the live UI model first, then fall back to environment settings."""
    setting_key, env_attr = _PROVIDER_MODEL_SETTINGS.get(
        provider, ("gemini_model", "GEMINI_MODEL")
    )
    try:
        runtime_model = SettingsService.get_instance().get(setting_key)
        if isinstance(runtime_model, str) and runtime_model.strip() and runtime_model.strip() not in {"***", "placeholder_key"}:
            return runtime_model.strip()
    except Exception:
        pass
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    return str(getattr(settings, env_attr, "unknown") or "unknown")

# Settings-key / env-var pairs proving a cloud provider is credentialed.
_PROVIDER_KEY_HINTS = {
    "gemini": ("gemini_api_key", "GEMINI_API_KEY"),
    "openai": ("openai_api_key", "OPENAI_API_KEY"),
    "openrouter": ("openrouter_api_key", "OPENROUTER_API_KEY"),
    "mistral": ("mistral_api_key", "MISTRAL_API_KEY"),
    "nvidia": ("nvidia_api_key", "NVIDIA_API_KEY"),
    "vertex": ("vertex_api_key", "VERTEX_API_KEY"),
    "claude": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
    "groq": ("groq_api_key", "GROQ_API_KEY"),
}


class ModelRouter:
    """
    Routes agents to optimal LLM with local-first fallback.

    Priority order:
    1. If agent is assigned LOCAL → try local first, fallback to cloud
    2. If agent is assigned CLOUD → use cloud directly
    3. If local provider is offline → auto-fallback to cloud
    """

    def __init__(self):
        settings = SettingsService.get_instance()
        self.fallback_provider = settings.get("default_llm_provider", "gemini")
        # Use the user's default provider as cloud fallback instead of hardcoded gemini
        self.cloud_fallback = self.fallback_provider if self.fallback_provider in CLOUD_PROVIDERS else "gemini"

        # Local LLM health cache (+ timestamps for TTL refresh)
        self._local_health: Dict[str, bool] = {}
        self._local_health_ts: Dict[str, float] = {}

        # Laya tier pools (re-resolved per routing call so Settings UI
        # saves apply without a restart)
        self.fast_pool = list(FAST_POOL)
        self.general_pool = list(GENERAL_POOL)
        self.reasoning_pool = list(REASONING_POOL)

        # Load routing table
        self.agent_routing = dict(DEFAULT_AGENT_ROUTING)

        dynamic_map = settings.get("agent_routing", {})
        if dynamic_map:
            self.agent_routing.update(dynamic_map)
            logger.info(
                "Custom agent model map loaded from SettingsService",
                overrides=dynamic_map,
            )

        logger.info(
            "ModelRouter initialized (local-first)",
            routing_summary={
                "cloud_agents": [
                    k for k, v in self.agent_routing.items() if v in CLOUD_PROVIDERS
                ],
                "local_agents": [
                    k for k, v in self.agent_routing.items() if v in LOCAL_PROVIDERS
                ],
                "fallback": self.fallback_provider,
            },
        )

    def refresh_from_settings(self) -> None:
        """Re-read routing table + fallback from the Settings store.

        Called after Settings UI saves so routing changes apply WITHOUT a
        backend restart (previously the table was frozen at startup and
        saves silently did nothing until restart).
        """
        settings = SettingsService.get_instance()
        self.fallback_provider = settings.get("default_llm_provider", "gemini")
        self.cloud_fallback = self.fallback_provider if self.fallback_provider in CLOUD_PROVIDERS else "gemini"
        self.agent_routing = dict(DEFAULT_AGENT_ROUTING)
        dynamic_map = settings.get("agent_routing", {})
        if dynamic_map:
            self.agent_routing.update(dynamic_map)
        logger.info("router_refreshed_from_settings", fallback=self.fallback_provider)

    def _health_fresh(self, provider: str) -> Optional[bool]:
        """Cached health within TTL, else None (needs re-probe)."""
        ts = self._local_health_ts.get(provider)
        if ts is None or (time.monotonic() - ts) > _LOCAL_HEALTH_TTL_SECONDS:
            return None
        return self._local_health.get(provider)

    async def check_local_health(self, provider: str, *, force: bool = False) -> bool:
        """Check whether a local provider can generate a small completion (TTL-cached)."""
        if not force:
            cached = self._health_fresh(provider)
            if cached is not None:
                return cached
        settings = SettingsService.get_instance()

        if provider == "ollama":
            url = settings.get("ollama_base_url", "http://localhost:11434")

            # Only swap for Docker if actually running in Docker
            if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER"):
                url = url.replace("localhost", "host.docker.internal").replace(
                    "127.0.0.1", "host.docker.internal"
                )

            model = settings.get("ollama_model", "llama3.2")
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(
                        f"{url.rstrip('/')}/api/generate",
                        json={"model": model, "prompt": "Reply OK.", "stream": False,
                              "options": {"num_predict": 8, "temperature": 0}},
                    )
                    payload = resp.json() if resp.status_code == 200 else {}
                    healthy = bool(str(payload.get("response", "")).strip())
                    self._local_health["ollama"] = healthy
                    self._local_health_ts["ollama"] = time.monotonic()
                    return healthy
            except Exception:
                self._local_health["ollama"] = False
                self._local_health_ts["ollama"] = time.monotonic()
                return False

        elif provider == "lmstudio":
            url = settings.get("lmstudio_base_url", "http://localhost:1234/v1")
            url = url.rstrip("/")
            if not url.endswith("/v1"):
                url = f"{url}/v1"

            # Only swap for Docker if actually running in Docker
            if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER"):
                url = url.replace("localhost", "host.docker.internal").replace(
                    "127.0.0.1", "host.docker.internal"
                )

            try:
                model = settings.get("lmstudio_model", "")
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        f"{url}/chat/completions",
                        json={"model": model, "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                              "max_tokens": 256, "temperature": 0, "stream": False},
                    )
                    payload = resp.json() if resp.status_code == 200 else {}
                    choices = payload.get("choices", [])
                    message = choices[0].get("message", {}) if choices else {}
                    healthy = bool(str(message.get("content", "")).strip() or message.get("tool_calls"))
                    self._local_health["lmstudio"] = healthy
                    self._local_health_ts["lmstudio"] = time.monotonic()
                    if healthy:
                        await self._discover_lmstudio_context(client, url, model)
                    return healthy
            except Exception:
                self._local_health["lmstudio"] = False
                self._local_health_ts["lmstudio"] = time.monotonic()
                return False

        return True  # Cloud providers assumed always healthy

    @staticmethod
    def _lmstudio_context_from_models(payload: dict, model: str) -> Optional[int]:
        """Loaded context length for ``model`` from LM Studio's model listing."""
        items = payload.get("models") or payload.get("data") or []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("key") or item.get("id") or "")
            if model and ident != model:
                continue
            for inst in item.get("loaded_instances") or []:
                cfg = inst.get("config") if isinstance(inst, dict) else None
                value = (cfg or {}).get("context_length") if isinstance(cfg, dict) else None
                if value:
                    return int(value)
            value = item.get("loaded_context_length") or (
                item.get("max_context_length") if item.get("state") == "loaded" else None
            )
            if value:
                return int(value)
        return None

    async def _discover_lmstudio_context(self, client, url: str, model: str) -> None:
        """Record the loaded model's real context window (best-effort)."""
        from app.core.llm.model_registry import set_live_context_window

        root = url[:-3] if url.endswith("/v1") else url
        for path in ("/api/v1/models", "/api/v0/models"):
            try:
                resp = await client.get(f"{root}{path}", timeout=5.0)
                if resp.status_code != 200:
                    continue
                tokens = self._lmstudio_context_from_models(resp.json(), model)
                if tokens:
                    set_live_context_window("lmstudio", model, tokens)
                    logger.info("lmstudio_context_discovered", model=model, context_window=tokens)
                    return
            except Exception:
                continue

    def get_provider_for_agent(self, agent_name: str) -> str:
        """Get the preferred LLM provider for a specific agent"""
        provider = self.agent_routing.get(agent_name, self.fallback_provider)
        logger.debug("Agent routed", agent=agent_name, provider=provider)
        return provider

    async def get_provider_with_fallback(self, agent_name: str, est_tokens: int = 500) -> Tuple[str, bool]:
        """
        Get provider with local-first fallback.
        Returns (provider, used_fallback).

        If assigned to local → check health → fallback to cloud if offline.
        """
        preferred = self.get_provider_for_agent(agent_name)

        if preferred in LOCAL_PROVIDERS and self._provider_fits_tokens(preferred, est_tokens):
            is_healthy = await self.check_local_health(preferred)
            if is_healthy:
                logger.info("Using local LLM", agent=agent_name, provider=preferred)
                return preferred, False
        elif (self._provider_credentialed(preferred)
              and self._provider_sendable(preferred, est_tokens)
              and self._provider_fits_tokens(preferred, est_tokens)):
            return preferred, False

        # Match fallback capability and context size to the Laya complexity tier.
        if est_tokens >= 16_000:
            tier_pool = [*self.reasoning_pool, *self.general_pool, *self.fast_pool]
        elif est_tokens >= 4_000:
            tier_pool = [*self.general_pool, *self.reasoning_pool, *self.fast_pool]
        else:
            tier_pool = [*self.fast_pool, *self.general_pool, *self.reasoning_pool]
        pool = [preferred, self.cloud_fallback, *tier_pool]
        alternate = await self._first_viable(list(dict.fromkeys(pool)), est_tokens)
        if alternate:
            logger.info(
                "Provider assignment fell back to viable route",
                agent=agent_name,
                attempted=preferred,
                provider=alternate,
            )
            return alternate, alternate != preferred
        logger.error("No credentialed and available LLM provider", agent=agent_name)
        return preferred, preferred != self.get_provider_for_agent(agent_name)

    def get_provider_for_task(self, task_type: str) -> str:
        """Get the LLM provider for a task type"""
        return TASK_TYPE_ROUTING.get(task_type.lower(), self.fallback_provider)

    def _provider_credentialed(self, provider: str) -> bool:
        """True if a cloud provider has an API key in settings or env."""
        if provider in LOCAL_PROVIDERS:
            return True
        hints = _PROVIDER_KEY_HINTS.get(provider)
        if not hints:
            return True
        settings = SettingsService.get_instance()
        skey, envkey = hints
        try:
            secret = settings.get(skey)
            if isinstance(secret, str) and secret.strip().lower() not in {"", "***", "placeholder_key", "none", "null"}:
                return True
        except Exception:
            pass
        # Vertex can also auth via ADC / project id without an API key
        if provider == "vertex":
            try:
                if settings.get("vertex_project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT"):
                    return True
            except Exception:
                pass
        env_secret = os.environ.get(envkey, "")
        return env_secret.strip().lower() not in {"", "***", "placeholder_key", "none", "null"}

    def _provider_sendable(self, provider: str, est_tokens: int = 500) -> bool:
        """True if the gateway rate-limiter currently allows this vendor."""
        try:
            from app.core.llm.llm_gateway import get_llm_gateway

            limiter = get_llm_gateway().limiters.get(provider)
            return limiter.can_send(est_tokens) if limiter else True
        except Exception:
            return True  # gateway unavailable → don't block routing

    def _provider_fits_tokens(self, provider: str, est_tokens: int) -> bool:
        """Reject routes whose configured model cannot hold the estimated request."""
        try:
            from app.config import get_settings
            from app.core.llm.model_registry import get_capabilities

            settings = get_settings()
            model = get_configured_model(provider, settings)
            return get_capabilities(model, provider).context_window >= est_tokens
        except Exception:
            return True

    async def _first_viable(self, pool: list, est_tokens: int = 500) -> Optional[str]:
        """First credentialed, uncongested route with enough context for the request."""
        for provider in pool:
            try:
                if not self._provider_fits_tokens(provider, est_tokens):
                    continue
                if provider in LOCAL_PROVIDERS:
                    if await self.check_local_health(provider):
                        return provider
                elif self._provider_credentialed(provider) and self._provider_sendable(provider, est_tokens):
                    return provider
            except Exception:
                continue
        return None

    async def get_provider_for_text(
        self, agent_name: str, task_text: str, _tier=None, est_tokens: Optional[int] = None,
    ) -> Tuple[str, bool]:
        """Laya System-1 tier pre-route (~33ms) across the full provider fleet.

        - ``simple`` → healthy local LLM, else cheapest fast cloud pool.
        - ``moderate`` → agent's assigned provider if viable, else general pool.
        - ``complex`` → reasoning pool (vertex/claude/openai-first).
        - Laya unavailable/abstains → legacy ``get_provider_with_fallback``.

        Returns (provider, used_fallback). Never raises.
        """
        # Refresh tier pools (env → Settings store → default) each call.
        self.fast_pool = _env_pool("LAYA_FAST_POOL", FAST_POOL)
        self.general_pool = _env_pool("LAYA_GENERAL_POOL", GENERAL_POOL)
        self.reasoning_pool = _env_pool("LAYA_REASONING_POOL", REASONING_POOL)
        # Callers that know the real request size (full prompt + system +
        # tools + output) pass it; task_text is only a classification excerpt.
        if not est_tokens:
            est_tokens = max(500, (len(task_text or "") + 2) // 3 + 2048)
        tier = None
        try:
            from app.core.laya.client import get_laya_client

            tier = _tier if _tier is not None else await get_laya_client().route_tier(task_text or agent_name)
            if tier is None:
                return await self.get_provider_with_fallback(agent_name, est_tokens)

            assigned = self.get_provider_for_agent(agent_name)

            if tier == "simple":
                for local in [assigned] if assigned in LOCAL_PROVIDERS else []:
                    if self._provider_fits_tokens(local, est_tokens) and await self.check_local_health(local):
                        return local, False
                for local in LOCAL_PROVIDERS:
                    if (local != assigned and self._provider_fits_tokens(local, est_tokens)
                            and await self.check_local_health(local)):
                        logger.info("laya_simple_local", agent=agent_name, provider=local)
                        return local, True
                fast = await self._first_viable(self.fast_pool, est_tokens)
                if fast:
                    logger.info("laya_simple_fast_cloud", agent=agent_name, provider=fast)
                    return fast, True
                return await self.get_provider_with_fallback(agent_name, est_tokens)

            if tier == "complex":
                pool = [p for p in self.reasoning_pool if p != assigned] + [assigned]
                pick = await self._first_viable(pool, est_tokens)
                if pick:
                    logger.info("laya_complex_routed", agent=agent_name, provider=pick)
                    return pick, pick != assigned
                return await self.get_provider_with_fallback(agent_name, est_tokens)

            # moderate: keep the assignment when viable, else general pool
            pool = [assigned] + [p for p in self.general_pool if p != assigned]
            pick = await self._first_viable(pool, est_tokens)
            if pick:
                return pick, pick != assigned
        except Exception as e:
            logger.warning("laya_complexity_route_failed", error=str(e))
        return await self.get_provider_with_fallback(agent_name, est_tokens)

    async def get_model_route_for_text(
        self, agent_name: str, task_text: str, est_tokens: Optional[int] = None,
    ):
        """Return provider, optional tier model override, and fallback status.

        ``est_tokens`` is the full request size; without it the size is
        guessed from ``task_text``, which callers usually truncate.
        """
        tier = None
        try:
            from app.core.laya.client import get_laya_client
            tier = await get_laya_client().route_tier(task_text or agent_name)
        except Exception as exc:
            logger.debug("laya_tier_unavailable_for_model_route", error=str(exc))
        if not est_tokens:
            est_tokens = max(500, (len(task_text or "") + 2) // 3 + 2048)
        if tier is None:
            provider, fallback = await self.get_provider_with_fallback(agent_name, est_tokens)
        else:
            provider, fallback = await self.get_provider_for_text(
                agent_name, task_text, _tier=tier, est_tokens=est_tokens
            )
        model = None
        try:
            setting = {"simple": "fast_model", "moderate": "general_model", "complex": "reasoning_model"}.get(tier)
            laya = SettingsService.get_instance().get("laya", {}) or {}
            raw = str(laya.get(setting, "") or "") if setting else ""
            if raw:
                configured_provider, _, configured_model = raw.partition(":")
                if configured_provider.strip().lower() == provider and configured_model.strip():
                    model = configured_model.strip()
        except Exception as exc:
            logger.debug("laya_model_override_unavailable", error=str(exc))
        return provider, model, fallback

    def get_client_for_agent(self, agent_name: str):
        """Get an initialized LLM client for a specific agent (sync version)"""
        from app.core.llm import get_llm_client

        provider = self.get_provider_for_agent(agent_name)

        # Quick sync health check for local providers
        if provider in LOCAL_PROVIDERS:
            cached = self._local_health.get(provider)
            if cached is False:
                logger.info(
                    "Local LLM cached as offline, using cloud", agent=agent_name
                )
                provider = self.cloud_fallback

        return get_llm_client(provider)

    def get_routing_table(self) -> Dict[str, str]:
        """Return the full routing table with health status"""
        return dict(self.agent_routing)

    def get_routing_table_with_health(self) -> Dict[str, dict]:
        """Return routing table with provider health info"""
        result = {}
        for agent, provider in self.agent_routing.items():
            is_local = provider in LOCAL_PROVIDERS
            healthy = self._local_health.get(provider) if is_local else None
            result[agent] = {
                "provider": provider,
                "is_local": is_local,
                "is_healthy": healthy,
                "health_status": "unknown" if healthy is None else ("healthy" if healthy else "unhealthy"),
                "fallback": (self.cloud_fallback if is_local and healthy is False else None),
            }
        return result


# Singleton
_model_router: Optional[ModelRouter] = None


def get_model_router() -> ModelRouter:
    """Get or create the model router singleton"""
    global _model_router
    if _model_router is None:
        _model_router = ModelRouter()
    return _model_router
