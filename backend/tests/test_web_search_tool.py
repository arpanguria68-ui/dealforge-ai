import pytest


@pytest.mark.asyncio
async def test_empty_web_search_is_reported_as_failure(monkeypatch):
    from app.core.tools.web_search import WebSearchTool

    tool = WebSearchTool()

    async def no_results(*args, **kwargs):
        return []

    monkeypatch.setattr(tool, "search", no_results)
    result = await tool.execute("test query")

    assert result.success is False
    assert result.data["results"] == []
    assert result.data["provider"] == "none"
    assert "no results" in result.error.lower()


@pytest.mark.asyncio
async def test_configured_serper_is_preferred_and_provider_is_visible(monkeypatch):
    from app.core.tools.web_search import WebSearchTool

    tool = WebSearchTool(serper_api_key="test-key")
    calls = []
    expected = [{
        "title": "Company filing",
        "url": "https://example.com/filing",
        "snippet": "Official filing",
        "published": "2026-01-01",
        "source": "Example",
        "search_provider": "serper",
    }]

    async def serper(query, num_results=5, **kwargs):
        calls.append("serper")
        return expected

    async def ddg(query, num_results=5):
        calls.append("ddg")
        return []

    monkeypatch.setattr(tool, "_serper_search", serper)
    monkeypatch.setattr(tool, "_ddg_fallback", ddg)
    result = await tool.execute("test query")

    assert result.success is True
    assert result.data["provider"] == "serper"
    assert result.data["results"] == expected
    assert calls == ["serper"]


@pytest.mark.asyncio
async def test_web_scraper_blocks_private_network_urls():
    from app.core.tools.tool_router import WebScraperTool

    result = await WebScraperTool().execute("http://127.0.0.1/health")

    assert result.success is False
    assert "private" in result.error.lower()


def test_financial_agent_extracts_values_from_scraped_primary_source():
    from app.agents.financial_analyst import FinancialAnalystAgent

    analysis = {
        "revenue_analysis": {"annual_revenue": None, "growth_rate": None},
        "profitability": {"gross_margin": None, "ebitda_margin": None},
    }
    tool_results = [{
        "name": "web_scraper",
        "success": True,
        "data": {
            "title": "Microsoft 2025 Annual Report",
            "url": "https://www.microsoft.com/investor/reports/ar25/index.html",
            "text": "Financially, it was a year of record performance. Revenue was $281.7 billion, up 15 percent. Operating income grew 17 percent.",
        },
    }]

    agent = FinancialAnalystAgent.__new__(FinancialAnalystAgent)
    agent._enforce_evidence_grounding(
        analysis, "Research Microsoft FY2025 revenue", {}, tool_results
    )

    assert analysis["revenue_analysis"]["annual_revenue"] == 281_700_000_000
    assert analysis["revenue_analysis"]["growth_rate"] == 15
    assert analysis["sources"][0]["url"].endswith("ar25/index.html")
