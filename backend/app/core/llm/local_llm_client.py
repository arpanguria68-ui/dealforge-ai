"""Local LLM Clients (Ollama & LM Studio)"""

from typing import List, Dict, Any, Optional, AsyncGenerator
import json
import httpx
import structlog
from app.config import get_settings

logger = structlog.get_logger()

# Local small models hallucinate more when sampling freely. Default to
# near-deterministic output; callers that want creativity pass temperature
# explicitly (the gateway forwards its own default when set).
LOCAL_DEFAULT_TEMPERATURE = 0.2


def _clamp_temperature(temperature: float) -> float:
    try:
        return max(0.0, min(1.0, float(temperature)))
    except (TypeError, ValueError):
        return LOCAL_DEFAULT_TEMPERATURE


def _extract_json_block(text: str) -> str:
    """Strip markdown fences / prose around a JSON payload (small-model hygiene)."""
    if not text:
        return ""
    # Drop ``` fence lines wherever they appear (start, end, or both)
    lines = [ln for ln in str(text).split("\n") if not ln.strip().startswith("```")]
    text = "\n".join(lines).strip()
    # Slice from the first opener to the last matching closer
    for i, ch in enumerate(text):
        if ch in "{[":
            closer = "}" if ch == "{" else "]"
            j = text.rfind(closer)
            if j > i:
                return text[i : j + 1].strip()
            return text[i:].strip()
    return text


def _safe_json_loads(raw: Any) -> Any:
    """Parse tool-call arguments defensively. Returns raw string on failure."""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        try:
            return json.loads(_extract_json_block(raw))
        except (json.JSONDecodeError, ValueError):
            logger.warning("tool_args_unparseable", preview=str(raw)[:120])
            return {"_raw": raw}


class OllamaClient:
    """Client for local Ollama instances"""

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None):
        settings = get_settings()
        url = base_url or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")

        import os

        if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER"):
            url = url.replace("localhost", "host.docker.internal").replace(
                "127.0.0.1", "host.docker.internal"
            )

        self.base_url = url.rstrip("/")
        self.model = model or getattr(settings, "OLLAMA_MODEL", "llama3")
        self.client = httpx.AsyncClient(timeout=120.0)
        self.provider = "ollama"
        self.max_context = 8000

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = LOCAL_DEFAULT_TEMPERATURE,
        max_tokens: int = 4096,
        json_mode: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate using Ollama Chat API"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": _clamp_temperature(temperature),
                "num_predict": max_tokens,
                # Without num_ctx Ollama uses its small default window and
                # silently drops the start of long prompts (system prompt and
                # instructions). Size it to the request, capped by OLLAMA_MAX_CTX.
                "num_ctx": _ollama_num_ctx(messages, tools, max_tokens),
            },
        }

        # Native JSON mode — eliminates need for regex parsing
        if json_mode:
            payload["format"] = "json"

        if tools:
            payload["tools"] = tools

        try:
            response = await self.client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

            message = data.get("message", {}) or {}
            content = message.get("content", "") or ""

            result: Dict[str, Any] = {"content": content, "raw_response": data}

            # Handle Ollama tool calls if present (guarded: small models
            # often return malformed JSON or plain strings)
            if message.get("tool_calls"):
                calls = []
                for tc in message["tool_calls"]:
                    fn = (tc or {}).get("function", {}) or {}
                    if not fn.get("name"):
                        continue
                    calls.append(
                        {"name": fn["name"], "args": _safe_json_loads(fn.get("arguments", {}))}
                    )
                if calls:
                    result["function_calls"] = calls

            return result

        except httpx.HTTPError as e:
            logger.error("Ollama connection failed. Is Ollama running?", error=str(e))
            raise


def _ollama_num_ctx(messages: List[Dict], tools: Optional[List[Dict]], max_tokens: int) -> int:
    import json as _json

    from app.core.llm.model_registry import ollama_max_ctx

    chars = sum(len(str(m.get("content") or "")) for m in messages)
    if tools:
        chars += len(_json.dumps(tools, default=str))
    needed = int(chars / 3.5) + int(max_tokens or 0) + 256
    size = 4096
    while size < needed and size < ollama_max_ctx():
        size *= 2
    return min(size, ollama_max_ctx())


class LMStudioClient:
    """Client for local LM Studio instances (OpenAI-compatible)"""

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None):
        from openai import AsyncOpenAI

        settings = get_settings()

        url = base_url or getattr(
            settings, "LMSTUDIO_BASE_URL", "http://localhost:1234/v1"
        )
        url = url.rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"

        import os

        if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER"):
            url = url.replace("localhost", "host.docker.internal").replace(
                "127.0.0.1", "host.docker.internal"
            )

        self.base_url = url
        # Model name often doesn't matter for LM Studio as it uses the loaded model, but we pass it anyway
        self.model = model or getattr(settings, "LMSTUDIO_MODEL", "local-model")
        reasoning = str(getattr(settings, "LMSTUDIO_REASONING", "off")).strip().lower()
        self.reasoning = reasoning if reasoning in {"off", "on"} else "off"

        # Initialize AsyncOpenAI with local LM Studio URL and dummy key
        self.client = AsyncOpenAI(base_url=self.base_url, api_key="lm-studio")
        self.native_base_url = self.base_url[:-3]
        self.native_client = httpx.AsyncClient(timeout=120.0)
        self.provider = "lmstudio"
        self.max_context = 12000

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = LOCAL_DEFAULT_TEMPERATURE,
        max_tokens: int = 4096,
        json_mode: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate with LM Studio, using native reasoning controls when possible."""

        if not tools and not json_mode:
            payload: Dict[str, Any] = {
                "model": self.model,
                "input": prompt,
                "temperature": _clamp_temperature(temperature),
                "max_output_tokens": max_tokens,
                "reasoning": self.reasoning,
                "store": False,
            }
            if system_prompt:
                payload["system_prompt"] = system_prompt
            try:
                response = await self.native_client.post(
                    f"{self.native_base_url}/api/v1/chat", json=payload
                )
                response.raise_for_status()
                data = response.json()
                content = "\n".join(
                    str(item.get("content", ""))
                    for item in data.get("output", [])
                    if item.get("type") == "message" and item.get("content")
                ).strip()
                if not content:
                    raise ValueError(
                        "LM Studio returned no user-facing message; reasoning may have exhausted the output budget"
                    )
                return {
                    "content": content,
                    "raw_response": data,
                    "provider_used": self.provider,
                    "model_used": data.get("model_instance_id", self.model),
                    "reasoning_mode": self.reasoning,
                    "usage": data.get("stats", {}),
                }
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {404, 405}:
                    raise
                logger.info(
                    "lmstudio_native_api_unavailable_using_compat_endpoint",
                    status=exc.response.status_code,
                )

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": _clamp_temperature(temperature),
            "max_tokens": max_tokens,
            "timeout": 120.0,
        }

        # LM Studio requires JSON Schema mode; the OpenAI-style json_object
        # format is rejected by current server versions.
        if json_mode:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": {"type": "object", "additionalProperties": True},
                },
            }

        # Many local models don't support tool calling natively, but forward
        # them when provided (gateway already warns on caps mismatch; agents
        # fall back to ReAct text format). Parsing of results is guarded below.
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        try:
            response = await self.client.chat.completions.create(**params)
            message = response.choices[0].message
            content = message.content or ""
            if json_mode and not content:
                # Some reasoning-tuned local models put the constrained JSON
                # payload in reasoning_content. Only accept a complete JSON
                # object here; never expose unstructured chain-of-thought.
                structured = getattr(message, "reasoning_content", "") or ""
                try:
                    if isinstance(json.loads(structured), dict):
                        content = structured
                except (TypeError, json.JSONDecodeError):
                    pass

            result: Dict[str, Any] = {
                "content": content,
                "raw_response": response,
            }

            if getattr(message, "tool_calls", None):
                calls = []
                for tc in message.tool_calls:
                    name = getattr(tc.function, "name", "")
                    if not name:
                        continue
                    calls.append(
                        {"name": name, "args": _safe_json_loads(getattr(tc.function, "arguments", ""))}
                    )
                if calls:
                    result["function_calls"] = calls

            finish_reason = getattr(response.choices[0], "finish_reason", None)
            if not result["content"].strip() and not result.get("function_calls"):
                reason = (
                    "reasoning exhausted the output budget"
                    if finish_reason == "length"
                    else "empty completion"
                )
                raise ValueError(f"LM Studio returned no user-facing answer ({reason})")

            return result

        except Exception as e:
            logger.error(
                "LM Studio connection failed. Is the Local Server running?",
                error=str(e),
            )
            raise
