"""Live web search integration for real-time market data."""
import os
import time
import asyncio
import httpx
import structlog
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.core.tools.base_tool import BaseTool, ToolResult

logger = structlog.get_logger(__name__)

def get_date_n_days_ago(days: int) -> str:
    """Get date N days ago in YYYY-MM-DD format."""
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

class WebSearchTool(BaseTool):
    """
    Search the web for real-time company news, market data, and analyst reports.
    Supports the configured Serper key, optional SerpAPI, SearXNG, then DuckDuckGo.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        serper_api_key: Optional[str] = None,
        searxng_url: Optional[str] = None,
        searxng_api_key: Optional[str] = None,
    ):
        super().__init__(
            name="web_search",
            description="Search the web for real-time company news, market data, competitor analysis, and industry trends."
        )
        self.api_key = api_key or os.getenv("SERPAPI_KEY")
        self.serper_api_key = serper_api_key or os.getenv("SERPER_API_KEY", "")
        self.searxng_url = searxng_url or os.getenv("SEARXNG_INSTANCE_URL", "")
        self.searxng_api_key = searxng_api_key or os.getenv("SEARXNG_API_KEY", "")
        try:
            from app.core.settings_service import SettingsService

            settings = SettingsService.get_instance()
            self.serper_api_key = self.serper_api_key or settings.get("serper_api_key", "")
            self.searxng_url = self.searxng_url or settings.get("searxng_instance_url", "")
            self.searxng_api_key = self.searxng_api_key or settings.get("searxng_api_key", "")
        except Exception:
            pass
        self.serpapi_url = "https://serpapi.com/search"
        self.serper_url = "https://google.serper.dev"
        self.logger = logger.bind(tool="WebSearchTool")

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "description": "Number of results", "default": 5},
                "search_type": {
                    "type": "string", 
                    "enum": ["general", "news"], 
                    "description": "Type of search (general or recent news)",
                    "default": "general"
                }
            },
            "required": ["query"]
        }

    async def execute(self, query: str, num_results: int = 5, search_type: str = "general") -> ToolResult:
        """Execute the search tool."""
        t0 = time.time()
        try:
            if search_type == "news":
                results = await self.search_news(query, num_results=num_results)
            else:
                results = await self.search(query, num_results=num_results)
            
            elapsed = round((time.time() - t0) * 1000, 1)
            if not results:
                return ToolResult(
                    success=False,
                    data={"query": query, "provider": "none", "results": []},
                    error="Web search returned no results. Check the search provider configuration or try a narrower query.",
                    execution_time_ms=elapsed,
                )
            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "provider": results[0].get("search_provider", "unknown"),
                    "results": results,
                },
                execution_time_ms=elapsed
            )
        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=f"Web search execution failed: {str(e)}",
                execution_time_ms=round((time.time() - t0) * 1000, 1)
            )

    async def search(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """Generic web search."""
        if self.serper_api_key:
            results = await self._serper_search(query, num_results=num_results)
            if results:
                return results
        if self.api_key:
            results = await self._serpapi_search(query, num_results=num_results)
            if results:
                return results
        if self.searxng_url:
            results = await self._searxng_search(query, num_results=num_results)
            if results:
                return results
        return await self._ddg_fallback(query, num_results=num_results)

    async def search_news(self, query: str, days: int = 7, num_results: int = 5) -> List[Dict[str, Any]]:
        """Search recent news articles."""
        date_filter = get_date_n_days_ago(days)
        enhanced_query = f"{query} after:{date_filter}"
        
        if self.serper_api_key:
            results = await self._serper_search(
                query, endpoint="news", num_results=num_results, days=days
            )
            if results:
                return results
        if self.api_key:
            results = await self._serpapi_search(enhanced_query, tbm="nws", num_results=num_results)
            if results:
                return results
        if self.searxng_url:
            results = await self._searxng_search(enhanced_query, num_results=num_results)
            if results:
                return results
        return await self._ddg_fallback(enhanced_query, num_results=num_results)

    async def _serper_search(
        self, query: str, endpoint: str = "search", num_results: int = 5, days: int = 7
    ) -> List[Dict[str, Any]]:
        """Search with the Serper provider configured in DealForge Settings."""
        try:
            payload = {"q": query, "num": num_results}
            if endpoint == "news":
                payload["tbs"] = f"qdr:{max(1, days)}d"
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.serper_url}/{endpoint}",
                    json=payload,
                    headers={"X-API-KEY": self.serper_api_key},
                )
                response.raise_for_status()
                data = response.json()
            rows = data.get("news", []) if endpoint == "news" else data.get("organic", [])
            return [
                {
                    "title": row.get("title", ""),
                    "url": row.get("link", ""),
                    "snippet": row.get("snippet", ""),
                    "published": row.get("date", "unknown"),
                    "source": row.get("source", "Serper"),
                    "search_provider": "serper",
                }
                for row in rows[:num_results]
            ]
        except Exception as e:
            self.logger.warning("serper_search_failed", error=str(e))
            return []

    async def _searxng_search(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """Query a configured SearXNG instance using its JSON search endpoint."""
        try:
            headers = {"Accept": "application/json"}
            if self.searxng_api_key:
                headers["Authorization"] = f"Bearer {self.searxng_api_key}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.searxng_url.rstrip('/')}/search",
                    params={"q": query, "format": "json"},
                    headers=headers,
                )
                response.raise_for_status()
                rows = response.json().get("results", [])
            return [
                {
                    "title": row.get("title", ""),
                    "url": row.get("url", ""),
                    "snippet": row.get("content", ""),
                    "published": row.get("publishedDate") or "unknown",
                    "source": row.get("engine", "SearXNG"),
                    "search_provider": "searxng",
                }
                for row in rows[:num_results]
            ]
        except Exception as e:
            self.logger.warning("searxng_search_failed", error=str(e))
            return []

    async def _serpapi_search(self, query: str, tbm: Optional[str] = None, num_results: int = 5) -> List[Dict[str, Any]]:
        """Execute search via SerpAPI."""
        params = {
            "q": query,
            "api_key": self.api_key,
            "num": num_results,
            "engine": "google"
        }
        if tbm:
            params["tbm"] = tbm

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.serpapi_url, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                results = []
                # Google News results
                if tbm == "nws":
                    for r in data.get("news_results", []):
                        results.append({
                            "title": r.get("title"),
                            "url": r.get("link"),
                            "snippet": r.get("snippet"),
                            "published": r.get("date"),
                            "source": r.get("source"),
                            "search_provider": "serpapi",
                        })
                # Standard Search results
                else:
                    for r in data.get("organic_results", []):
                        results.append({
                            "title": r.get("title"),
                            "url": r.get("link"),
                            "snippet": r.get("snippet"),
                            "published": r.get("date"),
                            "source": r.get("source"),
                            "search_provider": "serpapi",
                        })
                
                return results[:num_results]
        except Exception as e:
            self.logger.error("serpapi_search_failed", error=str(e))
            return await self._ddg_fallback(query, num_results=num_results)

    async def _ddg_fallback(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """Fallback to DuckDuckGo without blocking the async request loop."""
        try:
            def run_search():
                try:
                    from ddgs import DDGS
                except ImportError:
                    from duckduckgo_search import DDGS
                with DDGS(timeout=10) as ddgs:
                    return list(ddgs.text(query, max_results=num_results))

            raw = await asyncio.to_thread(run_search)
            
            return [
                {
                    "title": r.get("title"),
                    "url": r.get("href"),
                    "snippet": r.get("body"),
                    "published": "unknown",
                    "source": "DuckDuckGo",
                    "search_provider": "ddg",
                }
                for r in raw
            ]
        except Exception as e:
            self.logger.error("ddg_fallback_failed", error=str(e))
            return []
