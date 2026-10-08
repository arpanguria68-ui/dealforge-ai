"""Fail-soft async client over Laya's Router / Agent / remote HTTP.

Design rules:
- Never raise to callers: every public method returns ``None`` (or a
  heuristic fallback) when Laya is disabled, not installed, or errors.
- Never import torch/laya at module import time: all imports are lazy so
  ``import app.core.laya`` stays cheap and safe on edge containers.
- Thread-safe enough for asyncio: Router calls run in a worker thread via
  ``asyncio.to_thread`` since Laya inference is blocking.
- Three backends, in priority order:
  1. local in-process Router (needs ``pip install laya``)
  2. remote ``laya-serve`` over HTTP (needs only httpx; good for
     lightweight containers / Lambda without torch)
  3. deterministic keyword heuristic (always available, clearly marked)

Config (env, all optional):
  LAYA_ENABLED=true|false      master switch (default true; auto-off if unusable)
  LAYA_MODE=auto|local|remote|lmstudio|off   (default auto)
  LAYA_BASE_URL=http://laya-service:8000  remote server base URL
  LAYA_REMOTE_PATH=/v1/systemone  remote endpoint path (Jev-compatible APIs
                                  vary: laya-serve uses /v1/systemone,
                                  some OpenJev-compatible services use
                                  /api/evaluate with the same body shape)
  LAYA_API_KEY=...             bearer token for remote server
  LAYA_MODEL=multilingual      checkpoint override (default: router auto-picks)
  LAYA_PRELOAD=false           preload all checkpoints at first use (server only)
  LAYA_TIMEOUT_SECONDS=8       per-call timeout for remote mode
  LAYA_LMSTUDIO_URL=...        LM Studio base URL override (default: settings lmstudio_base_url)
  LAYA_LMSTUDIO_MODEL=...      LM Studio model override (default: settings lmstudio_model)
  LAYA_LMSTUDIO_TIMEOUT=120    per-call timeout for lmstudio mode (local gen is slow)
  LAYA_CACHE_TTL_SECONDS=300   decision cache TTL (0 disables caching)
  LAYA_CACHE_MAX_ENTRIES=2048  decision cache size (LRU eviction)
  LAYA_BREAKER_THRESHOLD=3     consecutive backend failures before the breaker opens
  LAYA_BREAKER_COOLDOWN_SECONDS=30  how long an open breaker short-circuits calls
  LAYA_WARMUP=true             at app startup, load the local Router/checkpoints in
                               the background so the first request doesn't pay it

Hot-path protections (the same brief/task/output is classified by the
planner, project manager, graph nodes, model router and confidence gate in
one run): identical decisions are served from a TTL cache, concurrent
identical requests share one in-flight call, and a per-backend circuit
breaker stops a dead remote/LM Studio endpoint from charging its full
timeout on every graph step.

Backends:
- local: in-process Laya checkpoints (needs ``pip install laya`` + torch).
  True System-1: single forward pass, calibrated confidences.
- remote: laya-serve over HTTP (no torch on the client).
- lmstudio: LM Studio chat model answers the same typed questions via
  constrained JSON. No torch needed; uses your loaded local model.
  Confidences are model-reported (uncalibrated) — treat thresholds loosely.
- heuristic/off: no decision engine; callers use legacy paths.
"""

import asyncio
import copy
import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LayaResult:
    """Normalized single-question outcome."""

    answer: Any = None            # choice label / score float / P(yes) float
    confidence: float = 0.0       # answer_confidence when provided, else heuristic
    backend: str = "off"          # local | remote | heuristic | off
    model: str = ""               # routing model name when known
    raw: Dict[str, Any] = field(default_factory=dict)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _laya_setting(attr: str, default: str = "") -> str:
    """Env wins; falls back to the Settings store ``laya`` object (Settings UI).

    Never raises — returns the default when the store is unavailable.
    """
    env_name = f"LAYA_{attr.upper()}"
    if os.environ.get(env_name) not in (None, ""):
        return os.environ[env_name]
    try:
        from app.core.settings_service import SettingsService

        laya = SettingsService.get_instance().get("laya", {}) or {}
        val = laya.get(attr, None)
        if val is None:
            return default
        return str(val)
    except Exception:
        return default


def laya_configured() -> bool:
    """True if the operator wants Laya attempted (mode != off, enabled)."""
    if _laya_setting("enabled", _env("LAYA_ENABLED", "true")).lower() in ("0", "false", "no", "off"):
        return False
    return _laya_setting("mode", _env("LAYA_MODE", "auto")).lower() != "off"


def _int_setting(attr: str, default: int) -> int:
    try:
        return int(float(_laya_setting(attr, _env(f"LAYA_{attr.upper()}", str(default))) or default))
    except (TypeError, ValueError):
        return default


class _BackendUnavailable(Exception):
    """Transport-level failure (counts against the circuit breaker)."""


class LayaDecisionClient:
    """Singleton-friendly fail-soft client. Use get_laya_client()."""

    _LMSTUDIO_DISCOVERY_TTL = 60.0

    def __init__(self) -> None:
        self._router: Any = None
        self._router_failed: bool = False
        self._local_available: Optional[bool] = None
        self._preload_attempted: bool = False
        self._lock = asyncio.Lock()
        # Decision cache: key -> (expires_at, answers)
        self._cache: "OrderedDict[str, tuple]" = OrderedDict()
        self._inflight: Dict[str, asyncio.Future] = {}
        # Circuit breaker per backend: {"failures": int, "open_until": float}
        self._breakers: Dict[str, Dict[str, float]] = {}
        self._http_client: Any = None
        self._http_loop: Any = None
        self._lmstudio_discovery: Optional[tuple] = None  # (expires_at, result)
        self._metrics: Dict[str, float] = {
            "calls": 0, "cache_hits": 0, "inflight_joins": 0, "backend_calls": 0,
            "failures": 0, "abstentions": 0, "breaker_short_circuits": 0,
            "latency_ms_total": 0.0,
        }

    # ── backend probing ──────────────────────────────────────────────
    def _mode(self) -> str:
        return _laya_setting("mode", _env("LAYA_MODE", "auto")).lower()

    def _remote_url(self) -> str:
        return _laya_setting("base_url", _env("LAYA_BASE_URL", "")).rstrip("/")

    def _remote_path(self) -> str:
        """Endpoint path for the remote decision API (Jev-compatible)."""
        path = _laya_setting("remote_path", _env("LAYA_REMOTE_PATH", "/v1/systemone"))
        path = (path or "/v1/systemone").strip() or "/v1/systemone"
        return path if path.startswith("/") else f"/{path}"

    def _api_key(self) -> str:
        return _laya_setting("api_key", _env("LAYA_API_KEY", ""))

    def _timeout(self, override: Optional[float] = None) -> float:
        if override:
            return override
        try:
            return float(_laya_setting("timeout_seconds", _env("LAYA_TIMEOUT_SECONDS", "8")) or 8)
        except (TypeError, ValueError):
            return 8.0

    def _model(self, override: Optional[str] = None) -> Optional[str]:
        return override or _laya_setting("model", _env("LAYA_MODEL", "")) or None

    @property
    def backend(self) -> str:
        if not laya_configured():
            return "off"
        mode = self._mode()
        if mode == "remote" and self._remote_url():
            return "remote"
        if mode == "local":
            return "local" if self._local_importable() else "off"
        if mode == "lmstudio":
            return "lmstudio"
        # auto: prefer local, then remote, then heuristic
        if self._local_importable():
            return "local"
        if self._remote_url():
            return "remote"
        return "heuristic"

    def _local_importable(self) -> bool:
        if self._local_available is not None:
            return self._local_available
        try:
            import importlib.util as _ilu

            self._local_available = _ilu.find_spec("laya") is not None
        except Exception:
            self._local_available = False
        return self._local_available

    async def _get_router(self) -> Optional[Any]:
        """Lazily build the local Router (thread-safe, once)."""
        if self._router is not None:
            return self._router
        if self._router_failed or not self._local_importable():
            return None
        async with self._lock:
            if self._router is not None:
                return self._router
            if self._router_failed:
                return None
            try:
                from laya import Router  # lazy: imports torch

                preload = _laya_setting("preload", _env("LAYA_PRELOAD", "false")).lower() in ("1", "true", "yes")
                self._router = await asyncio.to_thread(Router, preload=preload)
                self._preload_attempted = preload
                logger.info("laya_router_ready", preload=preload)
            except Exception as e:
                self._router_failed = True
                logger.warning("laya_router_unavailable", error=str(e))
                return None
        return self._router

    # ── cache / breaker / pooled HTTP ────────────────────────────────
    def _cache_key(self, backend: str, state: Any, questions: Dict[str, Any], model: Optional[str]) -> str:
        blob = json.dumps(
            {"b": backend, "m": model or "", "s": state, "q": questions},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        item = self._cache.get(key)
        if item is None:
            return None
        expires_at, answers = item
        if expires_at < time.monotonic():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return copy.deepcopy(answers)

    def _cache_put(self, key: str, answers: Optional[Dict[str, Any]]) -> None:
        ttl = _int_setting("cache_ttl_seconds", 300)
        if ttl <= 0 or not answers:
            return
        self._cache[key] = (time.monotonic() + ttl, copy.deepcopy(answers))
        self._cache.move_to_end(key)
        max_entries = max(1, _int_setting("cache_max_entries", 2048))
        while len(self._cache) > max_entries:
            self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        self._cache.clear()
        self._lmstudio_discovery = None

    def _breaker_open(self, backend: str) -> bool:
        br = self._breakers.get(backend)
        return bool(br and br.get("open_until", 0.0) > time.monotonic())

    def _record_success(self, backend: str) -> None:
        self._breakers.pop(backend, None)

    def _record_failure(self, backend: str, error: Exception) -> None:
        self._metrics["failures"] += 1
        br = self._breakers.setdefault(backend, {"failures": 0, "open_until": 0.0})
        br["failures"] += 1
        if br["failures"] >= max(1, _int_setting("breaker_threshold", 3)):
            cooldown = max(1, _int_setting("breaker_cooldown_seconds", 30))
            br["open_until"] = time.monotonic() + cooldown
            # Half-open after cooldown: the next call probes; one more failure re-opens.
            br["failures"] = max(0, br["failures"] - 1)
            logger.warning("laya_breaker_open", backend=backend, cooldown_s=cooldown, error=str(error)[:200])
        else:
            logger.warning("laya_predict_failed", backend=backend, error=str(error)[:200])

    def _http(self) -> Any:
        """Pooled AsyncClient bound to the running loop (keep-alive across calls)."""
        import httpx

        loop = asyncio.get_running_loop()
        if self._http_client is None or self._http_loop is not loop or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
            self._http_loop = loop
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None

    def stats(self) -> Dict[str, Any]:
        """Operational counters for the Settings/status endpoint."""
        m = dict(self._metrics)
        backend_calls = m.get("backend_calls") or 0
        latency_total = m.pop("latency_ms_total", 0.0)
        m["avg_backend_latency_ms"] = round(latency_total / backend_calls, 1) if backend_calls else None
        m["cache_entries"] = len(self._cache)
        m["cache_hit_rate"] = round(m["cache_hits"] / m["calls"], 4) if m["calls"] else None
        now = time.monotonic()
        m["breakers"] = {
            b: {"failures": int(v.get("failures", 0)),
                "open_for_s": round(max(0.0, v.get("open_until", 0.0) - now), 1)}
            for b, v in self._breakers.items()
        }
        return m

    async def warmup(self) -> bool:
        """Load the backend before the first real request (best-effort).

        The first local decision builds the Router and downloads checkpoints
        (~1 min observed), which otherwise lands on a user request.
        """
        if _laya_setting("warmup", _env("LAYA_WARMUP", "true")).lower() in ("0", "false", "no", "off"):
            return False
        if self.backend not in ("local", "remote"):
            return False
        started = time.monotonic()
        answers = await self.apredict(
            {"body": "warmup"}, {"warmup": {"type": "noul", "instructions": "Is this a warmup request?"}},
        )
        logger.info("laya_warmup_done", backend=self.backend, ok=bool(answers),
                    ms=int((time.monotonic() - started) * 1000))
        return bool(answers)

    # ── core predict ─────────────────────────────────────────────────
    async def apredict(
        self,
        state: Any,
        questions: Dict[str, Any],
        *,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Raw Laya predict; returns answers dict or None on any failure.

        Identical (backend, model, state, questions) requests are cached and
        coalesced; a failing backend is short-circuited by the breaker.
        """
        if not laya_configured() or not questions:
            return None
        backend = self.backend
        if backend not in ("local", "remote", "lmstudio"):
            return None
        self._metrics["calls"] += 1
        resolved_model = self._model(model)
        key = self._cache_key(backend, state, questions, resolved_model)
        cached = self._cache_get(key)
        if cached is not None:
            self._metrics["cache_hits"] += 1
            return cached
        pending = self._inflight.get(key)
        if pending is not None:
            self._metrics["inflight_joins"] += 1
            try:
                result = await asyncio.shield(pending)
            except Exception:
                return None
            return copy.deepcopy(result) if result else None
        if self._breaker_open(backend):
            self._metrics["breaker_short_circuits"] += 1
            return None

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        answers: Optional[Dict[str, Any]] = None
        t0 = time.monotonic()
        try:
            self._metrics["backend_calls"] += 1
            answers = await self._dispatch(backend, state, questions, resolved_model, timeout_seconds)
            self._record_success(backend)
            if answers:
                self._cache_put(key, answers)
            else:
                self._metrics["abstentions"] += 1
        except Exception as e:
            self._record_failure(backend, e)
            answers = None
        finally:
            self._metrics["latency_ms_total"] += (time.monotonic() - t0) * 1000
            self._inflight.pop(key, None)
            if not future.done():
                future.set_result(answers)
        return copy.deepcopy(answers) if answers else None

    async def _dispatch(
        self,
        backend: str,
        state: Any,
        questions: Dict[str, Any],
        model: Optional[str],
        timeout_seconds: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        """Single backend call. Raises on transport failure, None on abstention."""
        if backend == "local":
            router = await self._get_router()
            if router is None:
                raise _BackendUnavailable("local laya router unavailable")
            kw: Dict[str, Any] = {"model": model} if model else {}
            res = await asyncio.to_thread(router.predict, state, questions, **kw)
            return (res or {}).get("answers")
        if backend == "remote":
            return await self._predict_remote(state, questions, timeout_seconds)
        if backend == "lmstudio":
            return await self._predict_lmstudio(state, questions, timeout_seconds)
        return None

    async def _predict_remote(
        self,
        state: Any,
        questions: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        base = self._remote_url()
        if not base:
            raise _BackendUnavailable("LAYA_BASE_URL not configured")
        payload = {"state": state if isinstance(state, dict) else {"body": str(state)}, "questions": questions}
        headers = {}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = await self._http().post(
            f"{base}{self._remote_path()}", json=payload, headers=headers,
            timeout=self._timeout(timeout_seconds),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("answers") or data

    async def apredict_batch(
        self,
        requests: List[Dict[str, Any]],
        *,
        batch_size: int = 32,
    ) -> List[Optional[Dict[str, Any]]]:
        """Batched predict preserving input order; None per failed item.

        Cached items are served locally; only misses reach the backend.
        """
        if not laya_configured() or not requests:
            return [None] * len(requests)
        backend = self.backend
        if backend not in ("local", "remote", "lmstudio"):
            return [None] * len(requests)
        if self._breaker_open(backend):
            self._metrics["breaker_short_circuits"] += 1
            return [None] * len(requests)

        resolved_model = self._model()
        out: List[Optional[Dict[str, Any]]] = [None] * len(requests)
        keys: List[str] = []
        misses: List[int] = []
        for i, req in enumerate(requests):
            key = self._cache_key(backend, req.get("state"), req.get("questions", {}), resolved_model)
            keys.append(key)
            self._metrics["calls"] += 1
            hit = self._cache_get(key)
            if hit is not None:
                self._metrics["cache_hits"] += 1
                out[i] = hit
            else:
                misses.append(i)
        if not misses:
            return out

        pending = [requests[i] for i in misses]
        t0 = time.monotonic()
        self._metrics["backend_calls"] += 1
        try:
            results = await self._dispatch_batch(backend, pending, batch_size)
        except Exception as e:
            self._record_failure(backend, e)
            return out
        finally:
            self._metrics["latency_ms_total"] += (time.monotonic() - t0) * 1000
        if any(r for r in results):
            self._record_success(backend)
        elif results:
            # Every item failed: treat as a backend failure for the breaker.
            self._record_failure(backend, _BackendUnavailable("all batch items failed"))
        for idx, answers in zip(misses, results):
            out[idx] = answers
            if answers:
                self._cache_put(keys[idx], answers)
        return out

    async def _dispatch_batch(
        self, backend: str, requests: List[Dict[str, Any]], batch_size: int,
    ) -> List[Optional[Dict[str, Any]]]:
        if backend == "local":
            router = await self._get_router()
            if router is None:
                raise _BackendUnavailable("local laya router unavailable")
            if hasattr(router, "predict_batch"):
                res = await asyncio.to_thread(router.predict_batch, requests, batch_size=batch_size)
                return [(r or {}).get("answers") if isinstance(r, dict) else None for r in (res or [])] + \
                    [None] * max(0, len(requests) - len(res or []))
            # older laya: loop predict in worker threads, bounded concurrency
            sem = asyncio.Semaphore(4)

            async def _one(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                async with sem:
                    try:
                        r = await asyncio.to_thread(router.predict, req.get("state"), req.get("questions", {}))
                        return (r or {}).get("answers")
                    except Exception:
                        return None

            return list(await asyncio.gather(*[_one(r) for r in requests]))
        if backend == "remote":
            sem = asyncio.Semaphore(8)

            async def _post(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                async with sem:
                    try:
                        return await self._predict_remote(req.get("state"), req.get("questions", {}))
                    except Exception:
                        return None

            return list(await asyncio.gather(*[_post(r) for r in requests]))
        if backend == "lmstudio":
            # Local chat model: sem 2 — small servers serialize anyway, and
            # this keeps VRAM pressure predictable.
            sem = asyncio.Semaphore(2)

            async def _decide(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                async with sem:
                    try:
                        return await self._predict_lmstudio(req.get("state"), req.get("questions", {}))
                    except Exception:
                        return None

            return list(await asyncio.gather(*[_decide(r) for r in requests]))
        return [None] * len(requests)

    # ── LM Studio backend (typed decisions via constrained JSON) ────
    def _lmstudio_endpoint(self) -> tuple:
        """Resolve (base_url, model) for LM Studio. Never raises."""
        base = _laya_setting("lmstudio_url", _env("LAYA_LMSTUDIO_URL", ""))
        model = _laya_setting("lmstudio_model", _env("LAYA_LMSTUDIO_MODEL", ""))
        if not base or not model:
            try:
                from app.core.settings_service import SettingsService

                settings = SettingsService.get_instance()
                base = base or settings.get("lmstudio_base_url", "http://localhost:1234/v1")
                model = model or settings.get("lmstudio_model", "local-model")
            except Exception:
                base = base or "http://localhost:1234/v1"
                model = model or "local-model"
        base = (base or "").rstrip("/")
        if base and not base.endswith("/v1"):
            base = f"{base}/v1"
        if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER"):
            base = base.replace("localhost", "host.docker.internal").replace(
                "127.0.0.1", "host.docker.internal")
        return base, model

    @staticmethod
    def _select_lmstudio_model(
        models: List[Dict[str, Any]], laya_model: str, global_model: str,
    ) -> tuple[str, str, List[str]]:
        """Resolve Laya's model from the models LM Studio actually exposes."""
        chat_models = [
            item for item in models
            if isinstance(item, dict) and item.get("type", "llm") == "llm"
        ]
        loaded = []
        available_ids = []
        for item in chat_models:
            model_id = str(item.get("key") or item.get("id") or "").strip()
            if not model_id:
                continue
            available_ids.append(model_id)
            if item.get("loaded_instances"):
                loaded.append((model_id, item))
        loaded_ids = [model_id for model_id, _ in loaded]
        if laya_model and laya_model in loaded_ids:
            return laya_model, "laya_override_loaded", loaded_ids
        if global_model and global_model in loaded_ids:
            return global_model, "dealforge_model_loaded", loaded_ids
        if loaded:
            model_id, _ = min(
                loaded,
                key=lambda pair: int(pair[1].get("size_bytes") or 2**63 - 1),
            )
            return model_id, "smallest_loaded_chat_model", loaded_ids
        if laya_model and laya_model in available_ids:
            return laya_model, "laya_override_available", loaded_ids
        if global_model and global_model in available_ids:
            return global_model, "dealforge_model_available", loaded_ids
        if available_ids:
            return available_ids[0], "first_available_chat_model", loaded_ids
        return laya_model or global_model, "configured_fallback", loaded_ids

    def _global_lmstudio_model(self) -> str:
        if os.environ.get("LMSTUDIO_MODEL"):
            return os.environ["LMSTUDIO_MODEL"].strip()
        try:
            from app.core.settings_service import SettingsService

            return str(SettingsService.get_instance().get("lmstudio_model", "") or "").strip()
        except Exception:
            return ""

    async def _resolve_lmstudio_endpoint(self) -> tuple[str, str, str, List[str]]:
        """Use a loaded LM Studio model, adapting when its active model changes."""
        import httpx

        base, configured_model = self._lmstudio_endpoint()
        laya_override = _laya_setting("lmstudio_model", _env("LAYA_LMSTUDIO_MODEL", "")).strip()
        global_model = self._global_lmstudio_model()
        # Model discovery is cached briefly: without it every decision paid an
        # extra /api/v1/models round trip before the actual completion.
        signature = (base, configured_model, laya_override, global_model)
        cached = self._lmstudio_discovery
        if cached and cached[0] > time.monotonic() and cached[1] == signature:
            return cached[2]
        root = base[:-3] if base.endswith("/v1") else base
        try:
            response = await self._http().get(f"{root}/api/v1/models", timeout=3.0)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", []) if isinstance(payload, dict) else []
            selected, source, loaded = self._select_lmstudio_model(
                models, laya_override, global_model
            )
            result = (base, selected or configured_model, source, loaded)
            self._lmstudio_discovery = (time.monotonic() + self._LMSTUDIO_DISCOVERY_TTL, signature, result)
            return result
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("laya_lmstudio_model_discovery_failed", error_type=type(exc).__name__)
            return base, configured_model, "configured_fallback", []

    def _lmstudio_timeout(self, override: Optional[float] = None) -> float:
        if override:
            return override
        try:
            return float(_laya_setting(
                "lmstudio_timeout", _env("LAYA_LMSTUDIO_TIMEOUT", "120")) or 120)
        except (TypeError, ValueError):
            return 120.0

    @staticmethod
    def _decision_prompt(state: Any, questions: Dict[str, Any]) -> str:
        """Render Laya-style typed questions as a constrained-JSON prompt."""
        if isinstance(state, dict):
            text = state.get("body") or state.get("text") or json.dumps(state)[:4000]
        else:
            text = str(state)
        lines = [
            "You are a fast decision classifier. Answer ONLY with a single JSON object.",
            "No explanations, no markdown, no extra keys.",
            "",
            "State to evaluate:",
            f'"""{str(text)[:4000]}"""',
            "",
            "Questions (answer every one):",
        ]
        for i, (name, q) in enumerate(questions.items(), 1):
            qtype = (q or {}).get("type", "noul")
            instr = (q or {}).get("instructions", "")
            crit = (q or {}).get("criteria", "")
            if qtype == "choice" and isinstance(crit, dict):
                opts = ", ".join(f'"{k}": {v}' for k, v in list(crit.items())[:24])
                lines.append(
                    f'{i}. "{name}" (choice) — {instr} Options: {{{opts}}}. '
                    f'Respond with {{"choice": "<exact option key>", "confidence": 0.0-1.0}}.')
            elif qtype == "score" and isinstance(crit, list):
                levels = ", ".join(f"{n}={lv}" for n, lv in enumerate(crit))
                lines.append(
                    f'{i}. "{name}" (score) — {instr} Levels: [{levels}]. '
                    f'Respond with {{"score": <level number>, "confidence": 0.0-1.0}}.')
            else:
                lines.append(
                    f'{i}. "{name}" (yes/no) — {instr} '
                    f'Respond with {{"noul": <probability 0.0-1.0 that the answer is YES>, "confidence": 0.0-1.0}}.')
        lines.append("")
        lines.append("Return one JSON object keyed by question name.")
        return "\n".join(lines)

    @staticmethod
    def _normalize_lmstudio_answers(
        raw: Any, questions: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Convert a chat model's JSON into Laya wire-format answers.

        Invalid per-question values are dropped (strict: never invent a
        choice/score). Returns None when nothing usable remains.
        """
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                # last resort: first {...} block
                try:
                    start = raw.index("{")
                    raw = json.loads(raw[start: raw.rindex("}") + 1])
                except (ValueError, TypeError):
                    return None
        if not isinstance(raw, dict):
            return None
        # tolerate {"answers": {...}} wrapper
        if "answers" in raw and isinstance(raw["answers"], dict):
            raw = raw["answers"]
        out: Dict[str, Any] = {}
        for name, q in (questions or {}).items():
            item = raw.get(name)
            if not isinstance(item, dict):
                continue
            qtype = (q or {}).get("type", "noul")
            try:
                conf = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            conf = max(0.0, min(1.0, conf))
            if qtype == "choice":
                crit = (q or {}).get("criteria", {}) or {}
                val = str(item.get("choice", ""))
                # exact match, else case-insensitive, else drop
                if val not in crit:
                    lower = {k.lower(): k for k in crit}
                    val = lower.get(val.lower(), "")
                if val:
                    out[name] = {"choice": val, "confidence": conf,
                                 "answer_confidence": conf}
            elif qtype == "score":
                crit = (q or {}).get("criteria", []) or []
                try:
                    idx = int(float(item.get("score", -1)))
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(crit):
                    out[name] = {"score": float(idx), "confidence": conf,
                                 "answer_confidence": conf}
            else:
                try:
                    p = float(item.get("noul", -1))
                except (TypeError, ValueError):
                    continue
                if 0.0 <= p <= 1.0:
                    out[name] = {"noul": p, "confidence": conf,
                                 "answer_confidence": conf}
        return out or None

    async def _predict_lmstudio(
        self,
        state: Any,
        questions: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        import httpx

        if not questions:
            return None
        base, model, model_source, _ = await self._resolve_lmstudio_endpoint()
        timeout = self._lmstudio_timeout(timeout_seconds)
        messages = [
            {"role": "system",
             "content": "You output only valid JSON. No prose, no fences."},
            {"role": "user",
             "content": self._decision_prompt(state, questions)},
        ]
        base_payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 800,
        }
        # LM Studio servers accept json_schema (not json_object); fall back
        # to unconstrained generation if the server rejects structured output.
        # The normalizer tolerates prose/fences either way.
        variants = [
            {"response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision",
                    "strict": False,
                    "schema": {"type": "object",
                               "additionalProperties": True},
                }}},
            {},
        ]
        client = self._http()
        content = ""
        last_error: Optional[Exception] = None
        for extra in variants:
            try:
                resp = await client.post(
                    f"{base}/chat/completions",
                    json={**base_payload, **extra}, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                content = msg.get("content", "") or ""
                if not content.strip():
                    # Reasoning models (e.g. bonsai) put the answer in
                    # reasoning_content; the conclusion is at the end.
                    content = str(msg.get("reasoning_content", "") or "")[-2000:]
                last_error = None
                break
            except Exception as e:
                last_error = e
                logger.warning("laya_lmstudio_variant_failed",
                               variant=list(extra) or ["unconstrained"],
                               error=str(e)[:150])
                if isinstance(e, httpx.TransportError):
                    break  # server unreachable: the next variant cannot succeed
                continue
        if last_error is not None:
            raise _BackendUnavailable(f"LM Studio decision call failed: {last_error}")
        answers = self._normalize_lmstudio_answers(content, questions)
        if answers:
            logger.info("laya_lmstudio_decided", n=len(answers), model=model, model_source=model_source)
        return answers

    # ── typed helpers ────────────────────────────────────────────────
    @staticmethod
    def _parse_choice(answers: Dict[str, Any], name: str) -> Optional[LayaResult]:
        a = (answers or {}).get(name) or {}
        if "choice" not in a:
            return None
        return LayaResult(
            answer=a.get("choice"),
            confidence=float(a.get("answer_confidence", a.get("confidence", 0.0)) or 0.0),
            model=str(a.get("model", "")),
            raw=a,
        )

    @staticmethod
    def _parse_score(answers: Dict[str, Any], name: str) -> Optional[LayaResult]:
        a = (answers or {}).get(name) or {}
        if "score" not in a:
            return None
        try:
            val = float(a.get("score"))
        except (TypeError, ValueError):
            return None
        return LayaResult(
            answer=val,
            confidence=float(a.get("answer_confidence", a.get("confidence", 0.0)) or 0.0),
            raw=a,
        )

    @staticmethod
    def _parse_noul(answers: Dict[str, Any], name: str) -> Optional[LayaResult]:
        a = (answers or {}).get(name) or {}
        if "noul" not in a:
            return None
        try:
            val = float(a.get("noul"))
        except (TypeError, ValueError):
            return None
        # P(yes)=0.1 is a confident "no": default confidence is the
        # distance from a coin flip, not P(yes) itself.
        default_conf = max(val, 1.0 - val)
        return LayaResult(
            answer=val,
            confidence=float(a.get("answer_confidence", default_conf) or 0.0),
            raw=a,
        )

    async def achoice(
        self, state: Any, name: str, instructions: str, criteria: Dict[str, str],
        *, model: Optional[str] = None,
    ) -> Optional[LayaResult]:
        answers = await self.apredict(
            state, {name: {"type": "choice", "instructions": instructions, "criteria": criteria}},
            model=model,
        )
        if not answers:
            return None
        res = self._parse_choice(answers, name)
        if res:
            res.backend = self.backend
        return res

    async def ascore(
        self, state: Any, name: str, instructions: str, levels: List[str],
        *, model: Optional[str] = None,
    ) -> Optional[LayaResult]:
        answers = await self.apredict(
            state, {name: {"type": "score", "instructions": instructions, "criteria": levels}},
            model=model,
        )
        if not answers:
            return None
        res = self._parse_score(answers, name)
        if res:
            res.backend = self.backend
        return res

    async def anoul(
        self, state: Any, name: str, instructions: str, *, model: Optional[str] = None,
    ) -> Optional[LayaResult]:
        answers = await self.apredict(
            state, {name: {"type": "noul", "instructions": instructions}},
            model=model,
        )
        if not answers:
            return None
        res = self._parse_noul(answers, name)
        if res:
            res.backend = self.backend
        return res

    # ── DealForge workflows ──────────────────────────────────────────
    async def triage_deal(self, brief: str) -> Optional[Dict[str, Any]]:
        """Fast intake triage: track + urgency + deep-dive flag. None if unavailable."""
        from app.core.laya.presets import DEAL_TRIAGE_QUESTIONS

        answers = await self.apredict({"body": brief[:4000]}, DEAL_TRIAGE_QUESTIONS)
        if not answers:
            return None
        track = self._parse_choice(answers, "track")
        urgency = self._parse_score(answers, "urgency")
        deep = self._parse_noul(answers, "needs_deep_dive")
        return {
            "track": track.answer if track else None,
            "track_confidence": track.confidence if track else 0.0,
            "urgency": urgency.answer if urgency else None,
            # Abstention is not a "no deep dive" decision.
            "needs_deep_dive": (deep.answer >= 0.5) if deep else None,
            "deep_dive_p": deep.answer if deep else None,
            "backend": self.backend,
        }

    async def gate_confidence(self, output: str) -> Optional[Dict[str, Any]]:
        """System-1 quality pre-gate. Returns dict or None if unavailable."""
        from app.core.laya.presets import CONFIDENCE_QUESTIONS

        answers = await self.apredict({"body": output[:4000]}, CONFIDENCE_QUESTIONS)
        if not answers:
            return None
        supported = self._parse_noul(answers, "well_supported")
        flags = self._parse_noul(answers, "has_red_flags")
        quality = self._parse_score(answers, "quality")
        # Map 0..2 score levels to 0..1
        q01 = None
        if quality is not None:
            q01 = max(0.0, min(1.0, quality.answer / 2.0))
        p_support = supported.answer if supported else 0.5
        p_flags = flags.answer if flags else 0.0
        combined = 0.5 * p_support + 0.3 * (q01 if q01 is not None else 0.5) + 0.2 * (1.0 - p_flags)
        return {
            "supported_p": p_support,
            "red_flag_p": p_flags,
            "quality_01": q01,
            "combined": round(combined, 4),
            "backend": self.backend,
        }

    async def route_complexity(self, task: str) -> Optional[str]:
        """Return 'local' | 'cloud' | None (None → caller keeps default routing)."""
        from app.core.laya.presets import COMPLEXITY_QUESTIONS

        answers = await self.apredict({"body": task[:2000]}, COMPLEXITY_QUESTIONS)
        if not answers:
            return None
        tier = self._parse_choice(answers, "tier")
        if tier and tier.answer in ("local", "cloud") and tier.confidence >= 0.55:
            return str(tier.answer)
        return None

    async def route_tier(self, task: str) -> Optional[str]:
        """3-way complexity tier for multi-provider fleets.

        Returns 'simple' | 'moderate' | 'complex' | None. Combines the
        local/cloud choice with the 0-2 complexity score from a single
        forward pass; abstains (None) below 0.55 choice confidence so the
        caller keeps its static routing.
        """
        from app.core.laya.presets import COMPLEXITY_QUESTIONS

        answers = await self.apredict({"body": task[:2000]}, COMPLEXITY_QUESTIONS)
        if not answers:
            return None
        tier = self._parse_choice(answers, "tier")
        level = self._parse_score(answers, "complexity")
        score = level.answer if level is not None else 1.0
        if tier is None or tier.confidence < 0.55:
            # Small chat backends (e.g. LM Studio 3B) sometimes answer only
            # one of the two questions. Fall back to the score alone when it
            # is decisive; otherwise abstain.
            if level is not None and level.confidence >= 0.55:
                if score <= 0.7:
                    return "simple"
                if score >= 1.3:
                    return "complex"
            return None
        if tier.answer == "local" and score <= 0.7:
            return "simple"
        if tier.answer == "cloud" and score >= 1.3:
            return "complex"
        return "moderate"

    async def suggest_tool_family(
        self, task: str, available_tools: Optional[List[Dict[str, str]]] = None
    ) -> Optional[LayaResult]:
        from app.core.laya.presets import TOOL_ROUTING_CRITERIA

        catalog = []
        for tool in (available_tools or [])[:40]:
            name = str(tool.get("name", ""))[:100]
            description = str(tool.get("description", ""))[:240]
            if name:
                catalog.append(f"{name}: {description}")
        body = task[:1600]
        if catalog:
            body += "\n\nAvailable tools (choose only when needed):\n" + "\n".join(catalog)
        return await self.achoice(
            {"body": body[:6000]},
            "family",
            "Which tool family best serves this task?",
            TOOL_ROUTING_CRITERIA,
        )

    async def rerank_chunks(
        self, query: str, chunks: List[Dict[str, Any]], *, top_k: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
        """Laya-batch relevance scoring. Returns reordered chunk dicts or None.

        Each chunk dict must have a ``content`` key; extra keys are preserved
        and ``laya_relevance`` (P(relevant)) is added.
        """
        if not chunks:
            return []
        from app.core.laya.presets import RAG_RELEVANCE_QUESTION

        reqs = [
            {
                "state": {"body": f"Q: {query[:800]}\nChunk: {(c.get('content') or '')[:1500]}"},
                "questions": RAG_RELEVANCE_QUESTION,
            }
            for c in chunks
        ]
        t0 = time.monotonic()
        results = await self.apredict_batch(reqs, batch_size=32)
        scored: List[Dict[str, Any]] = []
        any_ok = False
        for chunk, answers in zip(chunks, results):
            rel = self._parse_noul(answers or {}, "relevant")
            qual = self._parse_score(answers or {}, "quality")
            p = rel.answer if rel else None
            if p is None and qual is not None:
                p = max(0.0, min(1.0, qual.answer / 2.0))
            if p is None:
                p = 0.0
            else:
                any_ok = True
            out = dict(chunk)
            out["laya_relevance"] = round(float(p), 4)
            scored.append(out)
        if not any_ok:
            return None
        scored.sort(key=lambda c: c["laya_relevance"], reverse=True)
        logger.info(
            "laya_rerank_done",
            n=len(chunks),
            ms=int((time.monotonic() - t0) * 1000),
            backend=self.backend,
        )
        return scored[:top_k]


_laya_client: Optional[LayaDecisionClient] = None


def get_laya_client() -> LayaDecisionClient:
    """Process-wide singleton (cheap; real init is lazy)."""
    global _laya_client
    if _laya_client is None:
        _laya_client = LayaDecisionClient()
    return _laya_client
