import asyncio
import json
from types import SimpleNamespace

import pytest


class _Tool:
    def __init__(self, name):
        self.name = name
        self.description = name

    def get_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def execute(self, **kwargs):
        from app.core.tools.base_tool import ToolResult

        return ToolResult(True, kwargs)


@pytest.mark.asyncio
async def test_laya_gets_real_per_agent_tool_catalog(monkeypatch):
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter()
    router.register_tool(_Tool("web_search"))
    observed = {}

    class FakeLaya:
        async def suggest_tool_family(self, task, available_tools):
            observed["task"] = task
            observed["tools"] = available_tools
            return SimpleNamespace(answer="search", confidence=0.9)

    monkeypatch.setattr("app.core.laya.client.get_laya_client", lambda: FakeLaya())
    names = await router.suggest_tools("Find current market information", "market_researcher")

    assert names == ["web_search"]
    assert observed["tools"] == [{"name": "web_search", "description": "web_search"}]


@pytest.mark.asyncio
async def test_laya_family_prompt_includes_catalog_without_abstention_conflict(monkeypatch):
    from app.core.laya.client import LayaDecisionClient

    client = LayaDecisionClient()
    observed = {}

    async def fake_achoice(state, name, instructions, criteria):
        observed.update(state=state, name=name, instructions=instructions, criteria=criteria)
        return None

    monkeypatch.setattr(client, "achoice", fake_achoice)
    await client.suggest_tool_family(
        "Find current market news",
        [{"name": "web_search", "description": "Search current web sources"}],
    )

    assert "web_search: Search current web sources" in observed["state"]["body"]
    assert "Choose none" not in observed["instructions"]
    assert "none" in observed["criteria"]


@pytest.mark.asyncio
async def test_laya_incompatible_confident_family_does_not_widen_tool_access(monkeypatch):
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter()
    router.register_tool(_Tool("web_search"))

    class FakeLaya:
        async def suggest_tool_family(self, task, available_tools):
            return SimpleNamespace(answer="integration", confidence=0.95)

    monkeypatch.setattr("app.core.laya.client.get_laya_client", lambda: FakeLaya())
    assert await router.suggest_tools("Assess this company's market", "market_researcher") == []


@pytest.mark.asyncio
async def test_mcp_discovery_is_read_only_namespaced_and_agent_scoped(monkeypatch):
    from app.core.mcp import external_client
    from app.core.tools.tool_router import ToolRouter

    monkeypatch.setenv("MCP_TOKEN", "test-secret")
    monkeypatch.setenv("MCP_SERVERS_JSON", json.dumps([{
        "id": "research",
        "url": "http://localhost:9000/mcp",
        "headers_env": {"Authorization": "MCP_TOKEN"},
        "allowed_agents": ["market_researcher"],
        "tool_families": {"lookup": "search"},
    }]))
    observed = {}

    class Session:
        async def list_tools(self):
            readonly = SimpleNamespace(
                name="lookup",
                description="Search current company data",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
                annotations=SimpleNamespace(readOnlyHint=True),
            )
            mutating = SimpleNamespace(
                name="write_record", description="Write", inputSchema={},
                annotations=SimpleNamespace(readOnlyHint=False),
            )
            return SimpleNamespace(tools=[readonly, mutating])

    async def fake_session(server):
        observed["headers"] = server["headers"]
        yield Session()

    monkeypatch.setattr(external_client, "_session", fake_session)
    descriptors = await external_client.discover_tools()
    assert len(descriptors) == 1
    assert descriptors[0]["name"] == "mcp_research__lookup"
    assert observed["headers"] == {"Authorization": "test-secret"}

    router = ToolRouter()
    monkeypatch.setattr(external_client, "discover_tools", lambda: asyncio.sleep(0, result=descriptors))
    await router._discover_mcp_tools("risk_assessor")
    assert "mcp_research__lookup" not in router.list_tools("risk_assessor")[0:]

    await router._discover_mcp_tools("market_researcher")
    assert "mcp_research__lookup" in {
        schema["function"]["name"] for schema in router.list_tools("market_researcher")
    }

    class FakeLaya:
        async def suggest_tool_family(self, task, available_tools):
            assert any(t["name"] == "mcp_research__lookup" for t in available_tools)
            return SimpleNamespace(answer="search", confidence=0.9)

    monkeypatch.setattr("app.core.laya.client.get_laya_client", lambda: FakeLaya())
    schemas = await router.list_tools_for_task("Find company information", "market_researcher")
    assert [s["function"]["name"] for s in schemas] == ["mcp_research__lookup"]

    async def fake_call(server, remote_name, arguments):
        assert server["id"] == "research"
        return {"company": "Example Co"}

    monkeypatch.setattr(external_client, "call_tool", fake_call)
    results = await router.execute_function_calls(
        [{"name": "mcp_research__lookup", "args": {"query": "Example Co"}}],
        allowed_tools=["mcp_research__lookup"],
    )
    assert results[0].success is True
    assert results[0].data == {"company": "Example Co"}
    assert "mcp_research__lookup" not in {
        schema["function"]["name"] for schema in router.list_tools()
    }

    monkeypatch.setattr(external_client, "discover_tools", lambda: asyncio.sleep(0, result=[]))
    assert await router.list_tools_for_task("Find company information", "market_researcher") == []


def test_pm_downgrades_mcp_dependency_without_agent_authorized_matching_tool():
    from app.agents.project_manager import ProjectManagerAgent

    tasks = [{
        "title": "Fetch three years of financial statements",
        "description": "Retrieve audited financial statements and source periods.",
        "assigned_agent": "financial_analyst",
        "data_dependency": "mcp_auto_fetch",
        "mcp_source": "research",
    }]
    tools = [{
        "name": "mcp_research__lookup",
        "description": "Search company profile and market news",
        "server_id": "research",
        "allowed_agents": ["market_researcher"],
    }]

    checked = ProjectManagerAgent._validate_mcp_plan_dependencies(tasks, tools)

    assert checked[0]["data_dependency"] == "requires_user_upload"
    assert checked[0]["mcp_source"] is None
    assert "agent-authorized" in checked[0]["risk_flag"]

    supported = [{
        "name": "mcp_finance__financial_statements",
        "description": "Retrieve company financial statements for historical periods",
        "server_id": "finance",
        "allowed_agents": ["financial_analyst"],
    }]
    positive_task = {
        "title": "Fetch financial statements",
        "description": "Retrieve the company's historical financial statements.",
        "assigned_agent": "financial_analyst",
        "data_dependency": "mcp_auto_fetch",
        "mcp_source": "finance",
    }
    accepted = ProjectManagerAgent._validate_mcp_plan_dependencies([positive_task], supported)

    assert accepted[0]["data_dependency"] == "mcp_auto_fetch"
    assert accepted[0]["mcp_source"] == "finance"
