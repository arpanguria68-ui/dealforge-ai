"""
LLM Gateway — Central token budget, rate-limit, retry, and cost control layer.

All LLM calls should funnel through this gateway so we can enforce:
1. Rolling-window rate limits (RPM/TPM) per vendor
2. Exponential backoff + jitter on 429/5xx
3. Automatic fallback (cloud → cloud → local)
4. Hybrid compression (local summarize → cloud reason)
5. Daily budget guardrails
6. Response caching for deterministic (temperature=0) calls
"""

import time
import random
import hashlib
import json
import asyncio
from collections import deque
from typing import Dict, Any, Optional, List, Tuple, Callable
from dataclasses import dataclass, field
import structlog

from app.config import get_settings
from app.core.llm.model_registry import get_capabilities
from app.core.llm.context_guard import enforce_context_limit
from app.core.llm.token_counter import TokenCounter

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════
#  1. UsageWindow — Track requests/tokens in a rolling window
# ═══════════════════════════════════════════════════════════


class UsageWindow:
    """Sliding window counter for rate tracking."""

    def __init__(self, window_seconds: int):
        self.window: deque = deque()
        self.window_seconds = window_seconds

    def add(self, amount: int = 1):
        now = time.time()
        self.window.append((now, amount))
        self._prune(now)

    def total(self) -> int:
        now = time.time()
        self._prune(now)
        return sum(v for _, v in self.window)

    def _prune(self, now: float):
        cutoff = now - self.window_seconds
        while self.window and self.window[0][0] < cutoff:
            self.window.popleft()


# ═══════════════════════════════════════════════════════════
#  2. VendorLimiter — Per-provider rate + token limits
# ═══════════════════════════════════════════════════════════


@dataclass
class VendorLimits:
    """Rate limit thresholds for a vendor (set to ~85% of actual quota)."""

    max_rpm: int = 50  # Requests per minute
    max_tpm: int = 100_000  # Tokens per minute
    max_rpd: int = 10_000  # Requests per day
    max_tokens_month: int = 0  # 0 = unlimited


class VendorLimiter:
    """Enforce soft rate limits per vendor."""

    def __init__(self, name: str, limits: VendorLimits):
        self.name = name
        self.limits = limits
        self.req_minute = UsageWindow(60)
        self.tok_minute = UsageWindow(60)
        self.req_day = UsageWindow(86400)
        self.tok_month = UsageWindow(30 * 86400)
        self.total_cost_usd = 0.0

    def can_send(self, est_tokens: int = 500) -> bool:
        """Check if we're within soft limits."""
        if self.req_minute.total() + 1 > self.limits.max_rpm:
            logger.warning(
                "rate_limit_rpm",
                vendor=self.name,
                current=self.req_minute.total(),
                limit=self.limits.max_rpm,
            )
            return False
        if self.tok_minute.total() + est_tokens > self.limits.max_tpm:
            logger.warning(
                "rate_limit_tpm",
                vendor=self.name,
                current=self.tok_minute.total(),
                limit=self.limits.max_tpm,
            )
            return False
        if self.limits.max_rpd > 0 and self.req_day.total() + 1 > self.limits.max_rpd:
            logger.warning(
                "rate_limit_rpd",
                vendor=self.name,
                current=self.req_day.total(),
                limit=self.limits.max_rpd,
            )
            return False
        return True

    def register(self, tokens_used: int):
        """Record a completed call."""
        self.req_minute.add(1)
        self.tok_minute.add(tokens_used)
        self.req_day.add(1)
        self.tok_month.add(tokens_used)

    def get_usage(self) -> Dict[str, Any]:
        return {
            "vendor": self.name,
            "rpm": {"current": self.req_minute.total(), "limit": self.limits.max_rpm},
            "tpm": {"current": self.tok_minute.total(), "limit": self.limits.max_tpm},
            "rpd": {"current": self.req_day.total(), "limit": self.limits.max_rpd},
            "monthly_tokens": self.tok_month.total(),
        }


# ═══════════════════════════════════════════════════════════
#  3. Retry with Exponential Backoff + Jitter
# ═══════════════════════════════════════════════════════════

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def exponential_backoff_retry(
    fn: Callable,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Any:
    """Retry async function with capped exponential backoff + jitter."""
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            # Also check for httpx.HTTPStatusError
            if hasattr(e, "response"):
                status = getattr(e.response, "status_code", status)

            # Rate/quota failures are provider-capacity signals, not transient transport
            # failures. Fail over immediately instead of spending the report time budget
            # retrying a provider that has already rejected the request.
            if status not in RETRYABLE_STATUS or status == 429 or attempt >= max_retries:
                raise

            sleep = min(max_delay, base_delay * (2**attempt))
            sleep = sleep * (0.8 + 0.4 * random.random())  # ±20% jitter
            logger.warning(
                "llm_retry",
                attempt=attempt + 1,
                status=status,
                backoff_s=f"{sleep:.1f}",
                error_type=type(e).__name__,
            )
            await asyncio.sleep(sleep)
            attempt += 1


# ═══════════════════════════════════════════════════════════
#  4. Response Cache (temperature=0 deterministic calls)
# ═══════════════════════════════════════════════════════════


class ResponseCache:
    """Simple in-memory LRU cache for deterministic LLM responses."""

    def __init__(self, max_size: int = 500):
        self._cache: Dict[str, Tuple[str, float]] = {}
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(provider: str, messages: List[Dict], model: str) -> str:
        raw = json.dumps({"p": provider, "m": messages, "model": model}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, provider: str, messages: List[Dict], model: str) -> Optional[str]:
        key = self._key(provider, messages, model)
        if key in self._cache:
            self.hits += 1
            return self._cache[key][0]
        self.misses += 1
        return None

    def set(self, provider: str, messages: List[Dict], model: str, response: str):
        key = self._key(provider, messages, model)
        if len(self._cache) >= self.max_size:
            # Evict oldest
            oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
        self._cache[key] = (response, time.time())


# ═══════════════════════════════════════════════════════════
#  5. Token Counter (rough estimation)
# ═══════════════════════════════════════════════════════════


class TokenCounter:
    """Rough token estimation (~4 chars per token for English)."""

    @staticmethod
    def estimate(text: str) -> int:
        return max(1, len(text) // 4)

    @staticmethod
    def estimate_messages(messages: List[Dict]) -> int:
        total = 0
        for m in messages:
            total += TokenCounter.estimate(m.get("content", ""))
            total += 4  # overhead per message
        return total


# ═══════════════════════════════════════════════════════════
#  6. LLM Gateway — Central orchestration point
# ═══════════════════════════════════════════════════════════

# Default vendor limits (aligned with standard free tier quotas)
DEFAULT_VENDOR_LIMITS = {
    "gemini": VendorLimits(
        max_rpm=15, max_tpm=1_000_000, max_rpd=1_500
    ),  # Default to Flash limits
    "openai": VendorLimits(max_rpm=50, max_tpm=150_000, max_rpd=8_000),
    "openrouter": VendorLimits(max_rpm=50, max_tpm=200_000, max_rpd=10_000),
    "mistral": VendorLimits(max_rpm=5, max_tpm=400_000, max_rpd=4_000),
    "ollama": VendorLimits(max_rpm=999, max_tpm=999_999, max_rpd=999_999),
    "lmstudio": VendorLimits(max_rpm=999, max_tpm=999_999, max_rpd=999_999),
    "nvidia": VendorLimits(max_rpm=50, max_tpm=500_000, max_rpd=10_000),
    "vertex": VendorLimits(max_rpm=30, max_tpm=1_000_000, max_rpd=2_000),
    "claude": VendorLimits(max_rpm=20, max_tpm=200_000, max_rpd=1_000),
    "groq": VendorLimits(max_rpm=30, max_tpm=500_000, max_rpd=5_000),
}

# Fallback chain: if primary is over-quota, try these in order
FALLBACK_CHAIN = {
    "gemini": ["mistral", "openai", "openrouter", "ollama"],
    "openai": ["gemini", "mistral", "openrouter", "ollama"],
    "openrouter": ["gemini", "openai", "mistral", "ollama"],
    "mistral": ["gemini", "openai", "ollama"],
    "ollama": ["lmstudio", "gemini", "mistral", "nvidia"],
    "lmstudio": ["ollama", "openrouter", "nvidia", "gemini", "mistral"],
    "nvidia": ["gemini", "openai", "mistral"],
    "vertex": ["gemini", "openai", "mistral"],
    "claude": ["openai", "gemini", "mistral"],
    "groq": ["gemini", "openai", "mistral"],
}


class LLMGateway:
    """
    Central gateway that all code uses instead of calling Gemini/Mistral/Ollama directly.

    Features:
    - Token + request budgeting per vendor
    - Automatic fallback when quota is tight
    - Exponential backoff retry on 429/5xx
    - Response caching for deterministic calls
    - Hybrid compression (local summarize → cloud reason)
    - Usage analytics
    """

    def __init__(self):
        self.limiters: Dict[str, VendorLimiter] = {}
        self.cache = ResponseCache()
        self.counter = TokenCounter()
        self._call_log: deque = deque(maxlen=1000)

        # Initialize limiters for all vendors
        for vendor, limits in DEFAULT_VENDOR_LIMITS.items():
            self.limiters[vendor] = VendorLimiter(vendor, limits)

        logger.info("LLMGateway initialized", vendors=list(self.limiters.keys()))

    def update_vendor_limits(self, vendor: str, limits: VendorLimits):
        """Update rate limits for a vendor (e.g., when user changes tier)."""
        self.limiters[vendor] = VendorLimiter(vendor, limits)
        logger.info(
            "vendor_limits_updated",
            vendor=vendor,
            rpm=limits.max_rpm,
            tpm=limits.max_tpm,
        )

    async def call(
        self,
        provider: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        use_cache: bool = True,
        model: Optional[str] = None,
        json_mode: bool = False,
        allow_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Central LLM call — all requests go through here.

        Returns: {"content": str, "provider_used": str, "tokens_est": int,
                  "cached": bool, "fallback_used": bool, ...}
        """
        from app.core.llm import get_llm_client

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        est_in = self.counter.estimate_messages(messages)
        # Tool schemas count against the window and the vendor's token rate
        # limits, so include them when choosing a provider/fallback.
        tool_tokens = self.counter.estimate(json.dumps(tools, default=str)) if tools else 0
        est_total = est_in + max_tokens + tool_tokens

        # ── Resolve model name (F-009) ──
        settings = get_settings()
        if not model:
            model = self._get_model_name(provider, settings)

        # ── Cache check (only for temperature=0) ──
        if use_cache and temperature == 0:
            cached = self.cache.get(provider, messages, model)
            if cached:
                logger.info("llm_cache_hit", provider=provider)
                return {
                    "content": cached,
                    "provider_used": provider,
                    "tokens_est": est_total,
                    "cached": True,
                    "fallback_used": False,
                    "model_used": model,
                }

        # ── Rate limit check + fallback chain ──
        actual_provider = provider
        fallback_used = False

        # Dynamic limit adjustment for Gemini (Flash vs Pro)
        if provider == "gemini":
            settings = get_settings()
            current_model = settings.GEMINI_MODEL.lower()
            is_flash = "flash" in current_model

            # Switch limits if needed
            new_limits = VendorLimits(
                max_rpm=15 if is_flash else 2,
                max_tpm=1_000_000 if is_flash else 32_000,
                max_rpd=1_500 if is_flash else 50,
            )

            # Update internal limiter if limits differ
            if self.limiters["gemini"].limits != new_limits:
                self.limiters["gemini"].limits = new_limits
                logger.info(
                    "dynamic_gemini_limits_applied",
                    model=current_model,
                    is_flash=is_flash,
                )

        if not await self._provider_ready(provider, est_total):
            if not allow_fallback:
                return {
                    "content": "[Provider unavailable] The requested local provider is not ready; fallback is disabled for this request.",
                    "provider_used": provider,
                    "tokens_est": est_total,
                    "cached": False,
                    "fallback_used": False,
                    "error": "provider_unavailable_local_only",
                }
            actual_provider, fallback_used = await self._find_available_provider(provider, est_total)
            if actual_provider is None:
                return {
                    "content": "[Provider unavailable] No credentialed, healthy, unthrottled LLM provider is available.",
                    "provider_used": "none",
                    "tokens_est": est_total,
                    "cached": False,
                    "fallback_used": True,
                    "error": "no_provider_available",
                }

        # ── Capability Lookup (F-009) ──
        # actual_provider = self._resolve_provider(provider) # This line is redundant as actual_provider is already determined
        actual_model = model if actual_provider == provider else self._get_model_name(actual_provider, settings)
        caps = get_capabilities(actual_model, actual_provider)

        # ── Context window guard (F-004) ──
        prompt, system_prompt, was_truncated = enforce_context_limit(
            prompt=prompt,
            system_prompt=system_prompt,
            provider=actual_provider,
            model=actual_model,
            context_window=caps.context_window,
            max_output_tokens=max_tokens,
            extra_tokens=tool_tokens,
        )
        if was_truncated:
            logger.warning("prompt_was_truncated", provider=actual_provider)

        # ── JSON Mode Adaptation (F-003 / F-009) ──
        # Use native JSON mode if requested and supported
        use_json_mode = json_mode and caps.json_mode
        if json_mode and not caps.json_mode:
            logger.warning("model_lacks_native_json_support", model=model)
            # Fallback will be handled by ReAct style if tools are present
            # or manual formatting instructions in prompt.

        # ── Tool calling capability check ──
        if tools and not caps.tool_calling:
             logger.warning("model_lacks_tool_support", model=model)
             # Transition to text-based tool format or warn

        # ── Execute with retry ──
        client = get_llm_client(actual_provider)
        if actual_provider == provider and actual_model:
            # Provider-specific clients expose their configured model under one of these names.
            for attr in ("model", "model_name"):
                if hasattr(client, attr):
                    setattr(client, attr, actual_model)
                    break

        async def do_call():
            return await client.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=use_json_mode,
            )

        def has_output(candidate: Any) -> bool:
            if not isinstance(candidate, dict):
                return False
            content = candidate.get("content")
            calls = candidate.get("function_calls") or candidate.get("tool_calls")
            return bool(isinstance(content, str) and content.strip()) or bool(calls)

        def failure_reason(exc: Exception) -> str:
            if isinstance(exc, asyncio.TimeoutError):
                return "timed out"
            if isinstance(exc, ValueError) and "reasoning consumed" in str(exc).lower():
                return "reasoning exhausted the output budget before a visible answer"
            if isinstance(exc, ValueError) and "empty completion" in str(exc).lower():
                return "returned an empty completion"
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None) or status
            if status == 401:
                return "rejected credentials (401)"
            if status == 403:
                return "access or billing denied (403)"
            if status == 404:
                return "model or endpoint not found (404)"
            if status == 429:
                return "rate-limited or quota exhausted (429)"
            if isinstance(status, int) and status >= 500:
                return f"provider service error ({status})"
            return f"request failed ({type(exc).__name__})"

        result = None
        failures = []
        try:
            candidate = await exponential_backoff_retry(do_call)
            if not has_output(candidate):
                raise ValueError("Provider returned an empty completion")
            result = candidate
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            if hasattr(e, "response"):
                status = getattr(e.response, "status_code", status)
            is_auth_error = status in (401, 403) or any(
                kw in str(e).lower()
                for kw in ("unauthorized", "invalid api key", "authentication", "api key not valid")
            )
            logger.error(
                "llm_call_failed",
                provider=actual_provider,
                error_type=type(e).__name__,
                reason=failure_reason(e),
                auth_error=is_auth_error,
                status=status,
            )
            failures.append({"provider": actual_provider, "reason": failure_reason(e)})
            if not allow_fallback:
                raise RuntimeError(
                    f"Local provider {actual_provider} failed ({failure_reason(e)}); fallback is disabled for this request."
                ) from e
            # Walk the full fallback chain (not just ollama)
            for fallback_provider in self._fallback_candidates(actual_provider, est_total):
                if fallback_provider == actual_provider or not await self._provider_ready(fallback_provider, est_total):
                    continue
                try:
                    fallback_client = get_llm_client(fallback_provider)
                    candidate = await fallback_client.generate(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=use_json_mode,
                    )
                    if not has_output(candidate):
                        raise ValueError("Provider returned an empty completion")
                    result = candidate
                    logger.info(
                        "fallback_succeeded",
                        original=actual_provider,
                        fallback=fallback_provider,
                        auth_error=is_auth_error,
                    )
                    actual_provider = fallback_provider
                    actual_model = self._get_model_name(fallback_provider, get_settings())
                    fallback_used = True
                    break
                except Exception as fallback_err:
                    logger.warning(
                        "fallback_attempt_failed",
                        provider=fallback_provider,
                        error_type=type(fallback_err).__name__,
                        reason=failure_reason(fallback_err),
                    )
                    failures.append({"provider": fallback_provider, "reason": failure_reason(fallback_err)})

            if result is None:
                attempt_summary = "; ".join(
                    f"{item['provider']}: {item['reason']}" for item in failures
                )
                content = "[Error] No configured provider returned a usable response."
                if attempt_summary:
                    content = f"{content} Provider attempts: {attempt_summary}."
                return {
                    "content": content,
                    "provider_used": actual_provider,
                    "tokens_est": est_total,
                    "cached": False,
                    "fallback_used": fallback_used,
                    "error": "provider_generation_failed",
                    "failure_reasons": failures,
                }

        content = result.get("content", "")

        # Strip thinking tokens if present (F-001: DeepSeek-R1, QwQ, GLM5, etc.)
        if content:
            from app.core.llm.thinking_utils import detect_thinking_tag, extract_thinking
            detected_tag = detect_thinking_tag(content)
            if detected_tag:
                clean_content, thinking_content = extract_thinking(content, detected_tag)
                result["content"] = clean_content
                result["thinking_content"] = thinking_content
                result["thinking_stripped"] = True
                content = clean_content
                logger.debug(
                    "thinking_tokens_stripped",
                    provider=actual_provider,
                    tag=detected_tag,
                    thinking_length=len(thinking_content),
                )

        actual_tokens = self.counter.estimate(content) + est_in

        # Register usage
        if actual_provider in self.limiters:
            self.limiters[actual_provider].register(actual_tokens)

        # Cache deterministic responses
        if use_cache and temperature == 0 and content:
            self.cache.set(actual_provider, messages, model, content)

        # Log call
        self._call_log.append(
            {
                "provider": actual_provider,
                "tokens": actual_tokens,
                "fallback": fallback_used,
                "cached": False,
                "time": time.time(),
            }
        )

        return {
            **result,
            "provider_used": actual_provider,
            "tokens_est": actual_tokens,
            "cached": False,
            "fallback_used": fallback_used,
            "model_used": actual_model,
            # Callers can tell the model saw a shortened prompt.
            "context_truncated": was_truncated,
            "context_window": caps.context_window,
        }

    async def hybrid_reasoning(
        self,
        question: str,
        context: str,
        cloud_provider: str = None,
        max_context_tokens: int = 4000,
    ) -> Dict[str, Any]:
        """
        Hybrid pattern: compress with local LLM, then reason with cloud.
        Saves cloud TPM by sending summarized context instead of full docs.
        """
        if cloud_provider is None:
            settings = get_settings()
            cloud_provider = getattr(settings, "DEFAULT_LLM_PROVIDER", "gemini")

        context_tokens = self.counter.estimate(context)

        if context_tokens > max_context_tokens:
            # Step 1: Local compression
            logger.info(
                "hybrid_compress",
                original_tokens=context_tokens,
                threshold=max_context_tokens,
            )
            compress_result = await self.call(
                provider="ollama",
                prompt=f"Summarize the following context for a financial analyst to answer "
                f"this question: {question}\n\nContext:\n{context}",
                system_prompt="You are a concise financial summarizer. "
                "Extract only key facts, numbers, and relevant details. "
                "Output a bullet-point summary under 500 words.",
                max_tokens=2048,
                temperature=0.1,
            )
            context = compress_result.get("content", context[: max_context_tokens * 4])

        # Step 2: Cloud reasoning
        return await self.call(
            provider=cloud_provider,
            prompt=f"Question: {question}\n\nContext:\n{context}",
            system_prompt="You are a senior investment analyst at a top-tier firm.",
            max_tokens=2048,
            temperature=0.2,
        )

    def _fallback_candidates(self, original: str, est_tokens: int = 0) -> List[str]:
        """Use token-aware Laya pool order, then provider policy and remaining routes."""
        candidates = []
        try:
            from app.core.llm.model_router import get_model_router

            router = get_model_router()
            if est_tokens >= 16_000:
                candidates.extend(router.reasoning_pool)
                candidates.extend(router.general_pool)
                candidates.extend(router.fast_pool)
            elif est_tokens >= 4_000:
                candidates.extend(router.general_pool)
                candidates.extend(router.reasoning_pool)
                candidates.extend(router.fast_pool)
            else:
                candidates.extend(router.fast_pool)
                candidates.extend(router.general_pool)
                candidates.extend(router.reasoning_pool)
        except Exception:
            pass
        candidates.extend(FALLBACK_CHAIN.get(original, []))
        candidates.extend(["nvidia", "vertex", "claude", "groq", "openai", "openrouter", "mistral", "gemini", "ollama", "lmstudio"])
        candidates = [name for name in dict.fromkeys(candidates) if name != original]
        if est_tokens >= 8_000:
            candidates = [p for p in candidates if p not in ("ollama", "lmstudio")] + [
                p for p in candidates if p in ("ollama", "lmstudio")
            ]
        return candidates

    async def _provider_ready(self, provider: str, est_tokens: int) -> bool:
        """Check credentials, local generation health, and gateway rate limits."""
        try:
            from app.core.llm.model_router import LOCAL_PROVIDERS, get_model_router

            router = get_model_router()
            if provider in LOCAL_PROVIDERS:
                if not await router.check_local_health(provider):
                    return False
            elif not router._provider_credentialed(provider):
                return False
            settings = get_settings()
            model = self._get_model_name(provider, settings)
            if get_capabilities(model, provider).context_window < est_tokens:
                return False
            limiter = self.limiters.get(provider)
            return limiter is None or limiter.can_send(est_tokens)
        except Exception as exc:
            logger.warning(
                "provider_readiness_check_failed",
                provider=provider,
                error_type=type(exc).__name__,
            )
            return False

    async def _find_available_provider(
        self, original: str, est_tokens: int
    ) -> Tuple[Optional[str], bool]:
        """Walk configured fallback providers, skipping missing credentials and unhealthy locals."""
        for alt in self._fallback_candidates(original, est_tokens):
            if await self._provider_ready(alt, est_tokens):
                logger.info("fallback_provider", original=original, fallback=alt)
                return alt, True
        return None, True

    @staticmethod
    def _get_model_name(provider: str, settings) -> str:
        from app.core.llm.model_router import get_configured_model

        return get_configured_model(provider, settings)

    def get_usage_stats(self) -> Dict[str, Any]:
        """Return usage stats for all vendors + cache stats."""
        return {
            "vendors": {
                name: limiter.get_usage() for name, limiter in self.limiters.items()
            },
            "cache": {
                "hits": self.cache.hits,
                "misses": self.cache.misses,
                "size": len(self.cache._cache),
                "hit_rate": (
                    f"{self.cache.hits / max(1, self.cache.hits + self.cache.misses) * 100:.1f}%"
                ),
            },
            "recent_calls": len(self._call_log),
        }


# ═══════════════════════════════════════════════════════════
#  7. Singleton accessor
# ═══════════════════════════════════════════════════════════

_llm_gateway: Optional[LLMGateway] = None


def get_llm_gateway() -> LLMGateway:
    """Get or create the LLM gateway singleton."""
    global _llm_gateway
    if _llm_gateway is None:
        _llm_gateway = LLMGateway()
    return _llm_gateway
