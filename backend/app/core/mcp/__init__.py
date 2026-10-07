"""Legacy credential-backed API integrations used by DealForge data tools.

These providers are ordinary vendor HTTP APIs, not MCP protocol servers. Real
Streamable HTTP MCP discovery and tool calls live in ``external_client``.
"""

from __future__ import annotations
import os
import asyncio
from typing import Any, Optional
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

logger = structlog.get_logger()

# Registry of known MCP data providers
MCP_PROVIDERS = {
    "finnhub": {
        "name": "Finnhub",
        "description": "Real-time stock prices, company financials, earnings, news, and market sentiment",
        "url_env": "FINNHUB_MCP_URL",
        "api_key_env": "FINNHUB_API_KEY",
        "base_url": "https://finnhub.io/api/v1",
        "ping_path": "/quote?symbol=AAPL",
        "auth_method": "query_param",  # ?token=<key>
        "auth_param": "token",
        "capabilities": [
            "stock_price",
            "financials",
            "earnings",
            "news",
            "sentiment",
            "comps",
            "ipo",
        ],
    },
    "massive": {
        "name": "Massive.com",
        "description": "U.S. market data, ticker reference data, corporate actions, and market status",
        "url_env": "MASSIVE_MCP_URL",
        "api_key_env": "MASSIVE_API_KEY",
        "base_url": "https://api.massive.com",
        "ping_path": "/v1/marketstatus/now",
        "auth_method": "bearer",
        "capabilities": [
            "market_status",
            "stock_prices",
            "ticker_reference_data",
            "corporate_actions",
        ],
    },
    "daloopa": {
        "name": "Daloopa",
        "description": "AI-extracted financial data from 10-K/10-Q filings",
        "url_env": "DALOOPA_MCP_URL",
        "api_key_env": "DALOOPA_API_KEY",
        "base_url": "",
        "ping_path": "",
        "auth_method": "bearer",
        "capabilities": ["financials", "earnings", "guidance"],
    },
    "sp_global": {
        "name": "S&P Global Market Intelligence",
        "description": "Comprehensive financial data, credit ratings, market data",
        "url_env": "SP_GLOBAL_MCP_URL",
        "api_key_env": "SP_GLOBAL_API_KEY",
        "base_url": "",
        "ping_path": "",
        "auth_method": "bearer",
        "capabilities": ["financials", "comps", "credit", "deals"],
    },
    "pitchbook": {
        "name": "PitchBook",
        "description": "Private market data — VC, PE, M&A deals",
        "url_env": "PITCHBOOK_MCP_URL",
        "api_key_env": "PITCHBOOK_API_KEY",
        "base_url": "",
        "ping_path": "",
        "auth_method": "bearer",
        "capabilities": ["deals", "funds", "companies", "investors"],
    },
    "factset": {
        "name": "FactSet",
        "description": "Public market data, estimates, screening",
        "url_env": "FACTSET_MCP_URL",
        "api_key_env": "FACTSET_API_KEY",
        "base_url": "",
        "ping_path": "",
        "auth_method": "bearer",
        "capabilities": ["prices", "estimates", "screening", "events"],
    },
    "crunchbase": {
        "name": "Crunchbase",
        "description": "Startup funding, investors, founders",
        "url_env": "CRUNCHBASE_MCP_URL",
        "api_key_env": "CRUNCHBASE_API_KEY",
        "base_url": "",
        "ping_path": "",
        "auth_method": "bearer",
        "capabilities": ["funding", "investors", "acquisitions"],
    },
    "serper": {
        "name": "Serper.dev",
        "description": "Fast Google Search API for agentic web retrieval",
        "url_env": "SERPER_MCP_URL",
        "api_key_env": "SERPER_API_KEY",
        "base_url": "https://google.serper.dev",
        "ping_path": "/search?q=ping",
        "auth_method": "header",
        "auth_header": "X-API-KEY",
        "capabilities": ["web_search", "news_search", "place_search"],
    },
    "searxng": {
        "name": "SearXNG",
        "description": "Privacy-respecting meta-search engine (Local or Self-hosted)",
        "url_env": "SEARXNG_INSTANCE_URL",
        "api_key_env": "SEARXNG_API_KEY",  # Optional
        "base_url": "",
        "ping_path": "/status",
        "auth_method": "none",
        "capabilities": ["web_search", "private_search"],
    },
    "ddg": {
        "name": "DuckDuckGo",
        "description": "Privacy-focused search (No API key required)",
        "url_env": "DDG_MCP_URL",
        "api_key_env": "DDG_API_KEY",
        "base_url": "",
        "ping_path": "",
        "auth_method": "none",
        "capabilities": ["web_search"],
    },
    "fmp": {
        "name": "Financial Modeling Prep",
        "description": "Comprehensive financial statements, 150+ ratios, DCF, WACC, and market data",
        "url_env": "FMP_MCP_URL",
        "api_key_env": "FMP_API_KEY",
        "base_url": "https://financialmodelingprep.com/stable",
        "ping_path": "/profile?symbol=AAPL",
        "auth_method": "query_param",
        "auth_param": "apikey",
        "capabilities": [
            "financials",
            "valuation_models",
            "ratios",
            "real_time_prices",
        ],
    },
    "alpha_vantage": {
        "name": "Alpha Vantage",
        "description": "Prices, fundamentals, and 50+ technical indicators",
        "url_env": "ALPHA_VANTAGE_MCP_URL",
        "api_key_env": "ALPHA_VANTAGE_API_KEY",
        "base_url": "https://www.alphavantage.co",
        "ping_path": "/query?function=GLOBAL_QUOTE&symbol=AAPL",
        "auth_method": "query_param",
        "auth_param": "apikey",
        "capabilities": ["stock_price", "technical_indicators", "forex", "commodities"],
    },
    "financial_datasets": {
        "name": "Financial Datasets",
        "description": "Statements, real-time prices, news, crypto",
        "url_env": "FINANCIAL_DATASETS_MCP_URL",
        "api_key_env": "FINANCIAL_DATASETS_API_KEY",
        "base_url": "https://api.financialdatasets.ai",
        "ping_path": "/financial-statements/income-statements?ticker=AAPL&period=annual&limit=1",
        "auth_method": "header",
        "auth_header": "X-API-KEY",
        "capabilities": ["financials", "stock_price", "news", "crypto"],
    },
    "sec_api": {
        "name": "SEC API (sec-api.io)",
        "description": "Advanced SEC filing search, XBRL-to-JSON, 10-K/8-K full-text",
        "url_env": "SEC_API_MCP_URL",
        "api_key_env": "SEC_API_KEY",
        "base_url": "https://api.sec-api.io",
        "ping_path": "/mapping/ticker/AAPL",
        "auth_method": "query_param",
        "auth_param": "token",
        "capabilities": ["filing_search", "xbrl_to_json", "full_text", "filing_dd"],
    },
}

# In-memory store for runtime-configured API keys (set via Settings UI)
_runtime_keys: dict[str, str] = {}


async def initialize_provider(provider_name: str, api_key: str) -> dict:
    """
    Live-test an MCP provider key and persist it in _runtime_keys.
    Returns {ok, latency_ms, error?, provider, capabilities}.
    """
    import time

    if provider_name not in MCP_PROVIDERS:
        return {"ok": False, "error": f"Unknown provider: {provider_name}"}

    provider = MCP_PROVIDERS[provider_name]
    base_url = provider.get("base_url", "")
    ping_path = provider.get("ping_path", "")
    auth_method = provider.get("auth_method", "bearer")

    # Providers without a health endpoint can only confirm that the key was saved.
    if not base_url or not ping_path:
        _runtime_keys[provider_name] = api_key
        logger.info("provider_key_saved_without_live_test", provider=provider_name)
        return {
            "ok": True,
            "latency_ms": 0,
            "provider": provider["name"],
            "capabilities": provider["capabilities"],
            "note": "Key saved. Provider does not support live ping.",
        }

    if not HTTPX_AVAILABLE:
        return {"ok": False, "latency_ms": 0, "error": "httpx is required to validate provider credentials."}

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            if auth_method == "query_param":
                param_name = provider.get("auth_param", "token")
                resp = await client.get(
                    f"{base_url}{ping_path}", params={param_name: api_key}
                )
            elif auth_method == "header":
                header_name = provider.get("auth_header", "X-API-KEY")
                resp = await client.get(
                    f"{base_url}{ping_path}",
                    headers={header_name: api_key},
                )
            else:
                resp = await client.get(
                    f"{base_url}{ping_path}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if resp.status_code in (200, 201, 204):
            _runtime_keys[provider_name] = api_key
            logger.info("mcp_ping_ok", provider=provider_name, latency_ms=latency_ms)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "provider": provider["name"],
                "capabilities": provider["capabilities"],
            }
        else:
            _runtime_keys.pop(provider_name, None)
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "error": f"HTTP {resp.status_code}: {resp.text[:200].replace(api_key, '[redacted]')}",
            }
    except Exception as e:
        _runtime_keys.pop(provider_name, None)
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        safe_error = str(e).replace(api_key, "[redacted]")
        logger.warning("provider_ping_failed", provider=provider_name, error=safe_error[:200])
        return {"ok": False, "latency_ms": latency_ms, "error": safe_error[:200]}


def get_provider_status() -> list[dict]:
    """Return status of all registered MCP providers."""
    result = []
    for name, provider in MCP_PROVIDERS.items():
        env_key = os.environ.get(provider["api_key_env"], "")
        runtime_key = _runtime_keys.get(name, "")
        configured = bool(env_key or runtime_key)
        result.append(
            {
                "id": name,
                "name": provider["name"],
                "description": provider["description"],
                "capabilities": provider["capabilities"],
                "configured": configured,
                "key_source": (
                    "env" if env_key else ("runtime" if runtime_key else "none")
                ),
            }
        )
    return result


class MCPClient:
    """
    Legacy API-key provider adapter; this does not implement MCP JSON-RPC.
    """

    def __init__(self, provider_name: str):
        if provider_name not in MCP_PROVIDERS:
            raise ValueError(
                f"Unknown MCP provider: {provider_name}. Available: {list(MCP_PROVIDERS.keys())}"
            )
        self.provider = MCP_PROVIDERS[provider_name]
        # Prefer env var, then runtime (Settings UI), then empty
        env_url = os.environ.get(self.provider.get("url_env", ""), "")
        self.base_url = env_url or self.provider.get("base_url", "")
        env_key = os.environ.get(self.provider.get("api_key_env", ""), "")
        self.api_key = env_key or _runtime_keys.get(provider_name, "")
        self.provider_name = provider_name

    @property
    def is_configured(self) -> bool:
        """Check if this provider has the necessary credentials."""
        return bool(self.api_key)

    async def query(self, tool_name: str, params: dict) -> dict:
        """
        Make a structured data request to the MCP provider.

        Args:
            tool_name: The MCP tool to call (e.g., 'get_financials', 'search_company')
            params: Parameters for the tool call
        Returns:
            Response data from the provider
        """
        if not self.is_configured:
            logger.warning(
                "mcp_provider_not_configured",
                provider=self.provider_name,
                url_env=self.provider["url_env"],
                key_env=self.provider["api_key_env"],
            )
            return {
                "error": f"Provider '{self.provider['name']}' not configured. "
                f"Set {self.provider['url_env']} and {self.provider['api_key_env']} environment variables.",
                "data": None,
                "configured": False,
            }

        if not HTTPX_AVAILABLE:
            return {"error": "httpx not installed", "data": None}

        # Dispatch to local ToolRouter instead of JSON-RPC
        _PROVIDER_TO_TOOL = {
            "search_company": "company_data",
            "get_financials": "fetch_financial_statements",
            "search": "web_search",
            "stock_price": "finnhub_data",
        }

        mapped_tool = _PROVIDER_TO_TOOL.get(tool_name, tool_name)
        normalized_params = dict(params or {})

        if mapped_tool == "company_data" and "company_name" not in normalized_params:
            if normalized_params.get("name"):
                normalized_params["company_name"] = normalized_params.pop("name")

        if (
            mapped_tool == "fetch_financial_statements"
            and "ticker" not in normalized_params
        ):
            if normalized_params.get("company_id"):
                normalized_params["ticker"] = normalized_params.pop("company_id")

        if mapped_tool == "web_search" and "query" not in normalized_params:
            if normalized_params.get("q"):
                normalized_params["query"] = normalized_params.pop("q")

        if mapped_tool == "finnhub_data":
            if "symbol" not in normalized_params:
                symbol = normalized_params.get("ticker") or normalized_params.get(
                    "company_id"
                )
                if symbol:
                    normalized_params["symbol"] = symbol
            normalized_params.setdefault("endpoint", "quote")

        try:
            from app.core.tools.tool_router import ToolRouter
            from app.core.memory.pageindex_client import get_pageindex_client

            router = ToolRouter()
            router.register_default_tools(get_pageindex_client())

            # execute() takes a tool name and params dict.
            result = await router.execute(mapped_tool, normalized_params)

            if result.success:
                logger.info(
                    "mcp_query_success",
                    provider=self.provider_name,
                    tool=tool_name,
                    mapped=mapped_tool,
                )
                return {
                    "data": result.data,
                    "configured": True,
                    "error": None,
                }
            else:
                logger.warning("mcp_query_failed", tool=tool_name, error=result.error)
                return {
                    "error": result.error,
                    "data": None,
                    "configured": True,
                }
        except Exception as e:
            logger.error(
                "mcp_query_error",
                provider=self.provider_name,
                tool=tool_name,
                error=str(e),
            )
            return {"error": str(e), "data": None, "configured": True}


class MCPRouter:
    """
    Routes legacy vendor API requests. Real MCP servers use external_client.
    """

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}

    def get_client(self, provider: str) -> MCPClient:
        if provider not in self._clients:
            self._clients[provider] = MCPClient(provider)
        return self._clients[provider]

    def list_configured(self) -> list[str]:
        """Return list of providers that have credentials configured."""
        configured = []
        for name in MCP_PROVIDERS:
            try:
                client = self.get_client(name)
                if client.is_configured:
                    configured.append(name)
            except Exception:
                pass
        return configured

    async def search_company(self, company_name: str) -> dict:
        """Search for a company across all configured MCP providers."""
        results = {}
        for provider_name in self.list_configured():
            client = self.get_client(provider_name)
            result = await client.query(
                "search_company", {"name": company_name, "limit": 5}
            )
            if not result.get("error"):
                results[provider_name] = result["data"]

        if not results:
            return {
                "message": "No MCP providers configured. Add provider API keys to environment to enable live data.",
                "providers_available": list(MCP_PROVIDERS.keys()),
                "providers_configured": [],
            }
        return results

    async def get_financials(
        self, company_id: str, provider: Optional[str] = None
    ) -> dict:
        """Fetch financial data for a company."""
        providers_to_try = [provider] if provider else self.list_configured()
        for p in providers_to_try:
            client = self.get_client(p)
            result = await client.query("get_financials", {"company_id": company_id})
            if not result.get("error"):
                return result
        return {
            "error": "No financial data available from configured providers",
            "data": None,
        }

    async def web_search(self, query: str, provider: Optional[str] = None) -> dict:
        """
        Execute a web search across configured providers with fallback logic.
        Default priority: serper -> searxng -> ddg
        """
        # Get priority from runtime settings or use default
        priority = _runtime_keys.get("search_priority", ["serper", "searxng", "ddg"])

        if provider and provider in MCP_PROVIDERS:
            # If a specific provider is requested, put it first
            if provider in priority:
                priority.remove(provider)
            priority.insert(0, provider)

        errors = []
        for p_name in priority:
            try:
                client = self.get_client(p_name)
                # DDG and SearXNG might not need keys, but Serper does
                if p_name == "serper" and not client.is_configured:
                    continue

                logger.info("web_search_attempt", provider=p_name, query=query)

                # Note: In a real implementation, we would call the specific search tool
                # documented in the provider's MCP capability.
                result = await client.query("search", {"q": query})

                if not result.get("error"):
                    return {
                        "provider": p_name,
                        "data": result["data"],
                        "fallback": p_name != priority[0],
                    }
                errors.append(f"{p_name}: {result.get('error')}")
            except Exception as e:
                errors.append(f"{p_name}: {str(e)}")
                continue

        return {
            "error": "Web search failed across all providers",
            "details": errors,
            "data": None,
        }


# Singleton router instance
_mcp_router: Optional[MCPRouter] = None


def get_mcp_router() -> MCPRouter:
    """Get or create the global MCP router instance."""
    global _mcp_router
    if _mcp_router is None:
        _mcp_router = MCPRouter()
    return _mcp_router
