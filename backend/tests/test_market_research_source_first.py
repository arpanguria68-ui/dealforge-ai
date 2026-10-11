import json
import types

import pytest

from app.core.tools.base_tool import ToolResult


class _ResearchTools:
    def __init__(self, search_result, pages=None):
        self.search_result = search_result
        self.pages = pages or {}
        self.calls = []

    async def execute(self, name, params):
        self.calls.append((name, params))
        if name == "web_search":
            return self.search_result
        page = self.pages.get(params["url"])
        return page or ToolResult(success=False, data=None, error="page unavailable")


def _agent(tools):
    from app.agents.market_researcher import MarketResearcherAgent

    agent = MarketResearcherAgent.__new__(MarketResearcherAgent)
    agent.name = "market_researcher"
    agent.tools = tools
    agent.logger = types.SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
    agent.retrieve_context = _empty_context
    agent._build_system_prompt = lambda *a, **k: "Research only from retrieved sources."
    return agent


async def _empty_context(*args, **kwargs):
    return []


def _results():
    return ToolResult(success=True, data={
        "provider": "ddg",
        "results": [{
            "title": "EIA battery storage update",
            "url": "https://www.eia.gov/todayinenergy/detail.php?id=67925",
            "snippet": "U.S. utility-scale battery capacity reached 52 GW in 2026.",
            "published": "2026-08-07",
            "source": "EIA",
            "search_provider": "ddg",
        }],
    })


@pytest.mark.asyncio
async def test_market_research_collects_search_evidence_before_model(monkeypatch):
    from app.agents.market_researcher import MarketResearcherAgent

    tools = _ResearchTools(
        _results(),
        {"https://www.eia.gov/todayinenergy/detail.php?id=67925": ToolResult(
            success=True,
            data={"title": "EIA battery storage update", "text": "Capacity reached 52 GW."},
        )},
    )
    agent = _agent(tools)
    prompt_seen = {}

    async def synthesize(prompt, system_prompt):
        prompt_seen["prompt"] = prompt
        return {"content": json.dumps({
            "market_size": {"tam": None, "sam": None, "som": None, "growth_rate": None},
            "competitive_landscape": {},
            "industry_trends": [],
            "findings": [{"text": "Capacity reached 52 GW.", "source_ids": ["W1"]}],
        }), "provider_used": "lmstudio"}

    agent.generate_with_routed_fallback = synthesize
    quality_module = types.SimpleNamespace(AgentQualityStore=lambda: types.SimpleNamespace(
        initialize=lambda: _done(),
        get_historical_best_practices=lambda *a: _empty_practices(),
    ))
    monkeypatch.setitem(__import__("sys").modules, "app.core.quality.agent_quality_store", quality_module)

    result = await agent.run("Research one recent U.S. battery storage market development")

    assert result.success is True
    assert result.data["synthesis_status"] == "complete"
    assert result.data["sources"][0]["id"] == "W1"
    assert result.data["sources"][0]["evidence_level"] == "page_text_retrieved"
    assert result.data["findings"][0]["source_ids"] == ["W1"]
    assert result.data["findings"][0]["citations"][0]["url"].startswith("https://www.eia.gov/")
    assert "Retrieved web evidence" in prompt_seen["prompt"]
    assert [call[0] for call in tools.calls] == ["web_search", "web_scraper"]


@pytest.mark.asyncio
async def test_market_research_returns_cited_evidence_when_llm_unavailable(monkeypatch):
    from app.agents.market_researcher import MarketResearcherAgent

    tools = _ResearchTools(_results())
    agent = _agent(tools)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("no configured provider")

    agent.generate_with_routed_fallback = unavailable
    quality_module = types.SimpleNamespace(AgentQualityStore=lambda: types.SimpleNamespace(
        initialize=lambda: _done(),
        get_historical_best_practices=lambda *a: _empty_practices(),
    ))
    monkeypatch.setitem(__import__("sys").modules, "app.core.quality.agent_quality_store", quality_module)

    result = await agent.run("Research recent U.S. battery storage market developments")

    assert result.success is True
    assert result.data["synthesis_status"] == "partial_provider_unavailable"
    assert result.data["sources"][0]["url"].startswith("https://www.eia.gov/")
    assert result.data["market_size"]["tam"] is None
    assert result.data["investment_thesis"].startswith("Not assessed")


@pytest.mark.asyncio
async def test_market_research_drops_findings_with_fabricated_source_ids(monkeypatch):
    from app.agents.market_researcher import MarketResearcherAgent

    agent = _agent(_ResearchTools(_results()))
    agent.generate_with_routed_fallback = lambda *a, **k: _answer({
        "findings": [
            {"text": "Supported item", "source_ids": ["W1"]},
            {"text": "Unsupported item", "source_ids": ["FAKE-1"]},
        ],
    })
    quality_module = types.SimpleNamespace(AgentQualityStore=lambda: types.SimpleNamespace(
        initialize=lambda: _done(),
        get_historical_best_practices=lambda *a: _empty_practices(),
    ))
    monkeypatch.setitem(__import__("sys").modules, "app.core.quality.agent_quality_store", quality_module)

    result = await agent.run("Research recent U.S. battery storage market developments")

    assert result.data["findings"][0]["text"] == "Supported item"
    assert result.data["findings"][0]["source_ids"] == ["W1"]
    assert result.data["unsupported_findings"][0]["text"] == "Unsupported item"
    assert result.data["synthesis_status"] == "partial_unverified_synthesis"
    assert result.data["market_size"]["som"] is None
    assert result.data["competitive_landscape"]["key_players"] == []


@pytest.mark.asyncio
async def test_market_research_marks_insufficient_or_snippet_only_findings_partial(monkeypatch):
    from app.agents.market_researcher import MarketResearcherAgent

    agent = _agent(_ResearchTools(_results()))
    agent.generate_with_routed_fallback = lambda *a, **k: _answer({
        "market_size": {"som": 572900000, "growth_rate": 8.5},
        "investment_thesis": "Invest now.",
        "evidence_gaps": ["No independent second source confirms the 2026 figure."],
        "findings": [
            {"text": "Capacity has increased.", "source_ids": ["W1"]},
            {"text": "Demand is rising.", "source_ids": ["W1"]},
        ],
    })
    quality_module = types.SimpleNamespace(AgentQualityStore=lambda: types.SimpleNamespace(
        initialize=lambda: _done(),
        get_historical_best_practices=lambda *a: _empty_practices(),
    ))
    monkeypatch.setitem(__import__("sys").modules, "app.core.quality.agent_quality_store", quality_module)

    result = await agent.run("Retrieve three current developments in U.S. battery storage")

    assert result.data["requested_finding_count"] == 3
    assert result.data["synthesis_status"] == "partial_unverified_synthesis"
    assert result.data["page_text_source_count"] == 0
    assert result.data["snippet_only_source_count"] == 1
    assert result.data["market_size"]["som"] is None
    assert result.data["investment_thesis"].startswith("Not assessed")
    assert "2 source-linked findings" in result.data["provider_warning"]
    assert "0 of 1 sources had page text retrieved" in result.data["provider_warning"]
    assert "No independent second source confirms the 2026 figure." in result.data["data_gaps"]


def test_market_research_prompt_is_compact_and_does_not_request_unused_outputs():
    from app.agents.market_researcher import MarketResearcherAgent

    agent = _agent(_ResearchTools(_results()))
    prompt = agent._build_research_prompt(
        "Retrieve three current developments in U.S. battery storage",
        None,
        [],
        {"sources": [{"id": "W1", "text": "Capacity reached 52 GW."}]},
        {},
        {},
    )

    assert prompt.count('"id":"W1"') == 1
    assert '"findings"' in prompt
    assert '"tam"' not in prompt
    assert '"investment_thesis"' not in prompt
    assert "Target 3 distinct" in prompt


@pytest.mark.asyncio
async def test_search_engine_is_not_misreported_as_source_publisher():
    from app.agents.market_researcher import MarketResearcherAgent

    agent = _agent(_ResearchTools(_results()))
    sources = await agent._collect_sources(_results().data["results"])

    assert sources[0]["publisher"] == "EIA"


def test_requested_finding_count_accepts_distinct_recent_wording():
    from app.agents.market_researcher import MarketResearcherAgent

    assert MarketResearcherAgent._requested_finding_count(
        "Retrieve three distinct, recent developments in U.S. grid-scale battery storage"
    ) == 3


@pytest.mark.asyncio
async def test_market_research_fails_when_search_returns_no_records():
    from app.agents.market_researcher import MarketResearcherAgent

    tools = _ResearchTools(ToolResult(success=False, data={"results": []}, error="search blocked"))
    agent = _agent(tools)
    agent.generate_with_routed_fallback = lambda *a, **k: pytest.fail("must not synthesize without sources")

    result = await agent.run("Research a market")

    assert result.success is False
    assert result.data["synthesis_status"] == "failed_retrieval"
    assert "search blocked" in result.reasoning
    assert [call[0] for call in tools.calls] == ["web_search"]


async def _done():
    return None


async def _empty_practices():
    return []


async def _answer(data):
    return {"content": json.dumps(data), "provider_used": "lmstudio"}
