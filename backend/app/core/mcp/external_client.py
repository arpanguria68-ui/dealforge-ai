"""Read-only discovery and invocation for configured Streamable HTTP MCP servers."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


from app.core.tools.base_tool import BaseTool, ToolResult


class RemoteMCPTool(BaseTool):
    def __init__(self, name: str, description: str, schema: dict[str, Any], server: dict[str, Any], remote_name: str):
        super().__init__(name, description)
        self._schema = schema
        self.server = server
        self.remote_name = remote_name

    def get_parameters_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, **kwargs: Any) -> ToolResult:
        import time

        started = time.monotonic()
        try:
            data = await call_tool(self.server, self.remote_name, kwargs)
            return ToolResult(True, data, execution_time_ms=(time.monotonic() - started) * 1000)
        except Exception as exc:
            return ToolResult(False, None, error=f"MCP tool call failed: {exc}")


def configured_servers() -> list[dict[str, Any]]:
    """Read MCP endpoint definitions from MCP_SERVERS_JSON; secrets stay in env vars."""
    raw = os.getenv("MCP_SERVERS_JSON", "")
    if not raw:
        return []
    try:
        servers = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("mcp_server_config_invalid_json")
        return []
    if not isinstance(servers, list):
        return []

    valid = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        server_id, url = server.get("id"), server.get("url")
        parsed = urlparse(url or "")
        if (
            not isinstance(server_id, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", server_id)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            continue
        headers_env = server.get("headers_env", {})
        headers = {
            str(header): os.getenv(str(env_name), "")
            for header, env_name in headers_env.items()
            if isinstance(headers_env, dict) and os.getenv(str(env_name), "")
        } if isinstance(headers_env, dict) else {}
        valid.append({
            "id": server_id,
            "url": url,
            "headers": headers,
            "allowed_agents": server.get("allowed_agents", []),
            "tool_families": server.get("tool_families", {}),
        })
    return valid


def namespaced_tool_name(server_id: str, tool_name: str) -> str:
    clean_server = re.sub(r"[^a-zA-Z0-9_-]", "_", server_id)
    clean_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name)
    return f"mcp_{clean_server}__{clean_tool}"[:128]


async def _session(server: dict[str, Any]):
    """Create an initialized SDK session; kept as a helper for test substitution."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    transport = streamable_http_client(server["url"], headers=server["headers"])
    async with transport as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


# server-config hash -> (expires_at, tools). Agents call discover_tools() at
# the start of every tool loop; without this each call re-opened a session to
# every configured server (up to 10s each when one is down).
_DISCOVERY_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_DISCOVERY_FAILURE_TTL = 15.0


def _discovery_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("MCP_DISCOVERY_TTL_SECONDS", "60")))
    except ValueError:
        return 60.0


def clear_discovery_cache() -> None:
    _DISCOVERY_CACHE.clear()


async def discover_tools() -> list[dict[str, Any]]:
    """Return only read-only tools from servers reachable at discovery time.

    Results are cached per server configuration (MCP_DISCOVERY_TTL_SECONDS,
    default 60s); an unreachable server is re-probed after 15s.
    """
    import hashlib
    import time

    async def discover_server(server: dict[str, Any]) -> list[dict[str, Any]]:
        key = hashlib.sha256(json.dumps(server, sort_keys=True, default=str).encode()).hexdigest()
        cached = _DISCOVERY_CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        found = await _discover_server_uncached(server)
        ttl = _discovery_ttl() if found is not None else _DISCOVERY_FAILURE_TTL
        result = found or []
        if ttl > 0:
            _DISCOVERY_CACHE[key] = (time.monotonic() + ttl, result)
        return result

    async def _discover_server_uncached(server: dict[str, Any]) -> list[dict[str, Any]] | None:
        try:
            async with asyncio.timeout(10):
                async for session in _session(server):
                    response = await session.list_tools()
                    found = []
                    for tool in response.tools[:100]:
                        annotations = getattr(tool, "annotations", None)
                        if getattr(annotations, "readOnlyHint", None) is not True:
                            continue
                        if not isinstance(server["allowed_agents"], list) or not server["allowed_agents"]:
                            continue
                        family = server.get("tool_families", {}).get(tool.name)
                        if family not in {"financial", "search", "document", "legal_risk", "tech_esg", "reporting", "integration"}:
                            continue
                        schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
                        found.append({
                            "name": namespaced_tool_name(server["id"], tool.name),
                            "description": str(tool.description or "External read-only MCP tool")[:500],
                            "parameters": schema,
                            "server": server,
                            "remote_name": tool.name,
                            "family": family,
                        })
                    return found
        except Exception as exc:
            logger.warning("mcp_tool_discovery_failed", server=server["id"], error=str(exc))
        return None

    responses = await asyncio.gather(*(discover_server(server) for server in configured_servers()))
    found = [tool for response in responses for tool in response]
    unique = {}
    for tool in found:
        unique.setdefault(tool["name"], tool)
    return list(unique.values())[:100]


async def planning_tool_summaries() -> list[dict[str, Any]]:
    """Return only non-secret discovery metadata for the orchestrator and API."""
    tools = await discover_tools()
    return [
        {
            "name": tool["name"],
            "description": tool["description"][:300],
            "server_id": tool["server"]["id"],
            "family": tool["family"],
            "allowed_agents": list(tool["server"]["allowed_agents"]),
        }
        for tool in tools
    ]


async def call_tool(server: dict[str, Any], remote_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a previously discovered read-only tool using MCP call_tool."""
    async with asyncio.timeout(30):
        async for session in _session(server):
            result = await session.call_tool(remote_name, arguments=arguments)
            if getattr(result, "isError", False):
                raise RuntimeError("MCP server reported a tool execution error")
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                if len(json.dumps(structured, default=str)) > 100_000:
                    raise ValueError("MCP response exceeds the 100 KB result limit")
                return structured
            text_result = {
                "content": [
                    {"type": item.type, "text": item.text}
                    for item in (getattr(result, "content", []) or [])
                    if getattr(item, "type", None) == "text"
                ]
            }
            if len(json.dumps(text_result, default=str)) > 100_000:
                raise ValueError("MCP response exceeds the 100 KB result limit")
            return text_result
    raise RuntimeError("MCP session closed without a result")
