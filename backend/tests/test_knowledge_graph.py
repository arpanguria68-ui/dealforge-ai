"""Embedded SQLite knowledge graph: the default backend replacing Neo4j."""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.core.knowledge_graph.graph_store import NullGraphStore, SQLiteGraphStore


@pytest.fixture
def store(tmp_path):
    s = SQLiteGraphStore(str(tmp_path / "kg.db"))
    yield s
    s.close()


@pytest.mark.asyncio
async def test_agent_findings_round_trip(store):
    await store.initialize_deal("d1", "Acme Buyout", "software")
    await store.add_entity("d1", "financial_analyst_revenue", "Metric", {"value": 120.5, "agent": "financial_analyst"})
    await store.add_entity("d1", "Globex", "company", {"role": "competitor"})
    await store.add_entity("d1", "Mystery", "Spaceship", {})
    await store.add_risk("d1", "Customer concentration", 8, "Commercial", "Top client is 40% of revenue")
    await store.add_risk("d1", "Key person", 5, "Operational")

    summary = await store.deal_summary("d1")
    assert summary["deal"]["name"] == "Acme Buyout"
    assert summary["counts"] == {"Company": 1, "Entity": 1, "Metric": 1, "Risk": 2}
    assert [r["name"] for r in summary["top_risks"]] == ["Customer concentration", "Key person"]
    metrics = await store.query_current_facts("d1", "Metric")
    assert metrics[0]["properties"] == {"value": 120.5, "agent": "financial_analyst"}
    assert [r["name"] for r in await store.get_risks("d1", min_severity=6)] == ["Customer concentration"]


@pytest.mark.asyncio
async def test_nodes_are_scoped_per_deal(store):
    """Neo4j MERGEd on (label, name) globally, so deals overwrote each other's metrics."""
    await store.add_entity("d1", "financial_analyst_revenue", "Metric", {"value": 100})
    await store.add_entity("d2", "financial_analyst_revenue", "Metric", {"value": 999})

    d1 = await store.query_current_facts("d1", "Metric")
    assert d1[0]["properties"]["value"] == 100
    assert (await store.query_current_facts("d2", "Metric"))[0]["properties"]["value"] == 999


@pytest.mark.asyncio
async def test_properties_merge_and_temporal_validity(store):
    now = datetime.now(timezone.utc)
    await store.add_entity("d1", "Acme", "Company", {"hq": "NYC"})
    await store.add_entity("d1", "Acme", "Company", {"employees": 50})
    await store.add_entity("d1", "Old CEO", "Person", {"valid_until": (now - timedelta(days=1)).isoformat()})
    await store.add_entity("d1", "Future deal", "Company", {"valid_from": (now + timedelta(days=30)).isoformat()})

    facts = await store.query_current_facts("d1")
    assert [f["name"] for f in facts] == ["Acme"]
    assert facts[0]["properties"] == {"hq": "NYC", "employees": 50}


@pytest.mark.asyncio
async def test_writes_before_deal_init_are_kept_and_bad_names_ignored(store):
    await store.add_risk("d9", "Litigation", 7, "Legal")
    await store.add_entity("d9", None, "Company", {})
    assert (await store.get_deal("d9"))["deal_id"] == "d9"
    assert [f["name"] for f in await store.query_current_facts("d9")] == ["Litigation"]


@pytest.mark.asyncio
async def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "kg.db")
    first = SQLiteGraphStore(path)
    await first.add_risk("d1", "FX exposure", 4, "Financial")
    first.close()
    second = SQLiteGraphStore(path)
    assert (await second.get_risks("d1"))[0]["name"] == "FX exposure"
    second.close()


def test_backend_selection_and_neo4j_not_required(monkeypatch, tmp_path):
    from app.core.knowledge_graph import service

    monkeypatch.setenv("KG_SQLITE_PATH", str(tmp_path / "kg.db"))
    monkeypatch.delenv("KG_BACKEND", raising=False)
    assert isinstance(service.build_knowledge_graph(), SQLiteGraphStore)
    assert isinstance(service.build_knowledge_graph("off"), NullGraphStore)
    # Building the default backend must not import the neo4j driver.
    monkeypatch.setitem(sys.modules, "neo4j", None)
    assert isinstance(service.build_knowledge_graph("sqlite"), SQLiteGraphStore)


@pytest.mark.asyncio
async def test_neo4j_backend_backs_off_instead_of_redialling(monkeypatch):
    from app.core.knowledge_graph.neo4j_client import Neo4jClient

    attempts = []

    class _Driver:
        async def verify_connectivity(self):
            attempts.append(1)
            raise ConnectionError("refused")

    class _GraphDatabase:
        @staticmethod
        def driver(uri, auth):
            return _Driver()

    monkeypatch.setitem(sys.modules, "neo4j", type(sys)("neo4j"))
    sys.modules["neo4j"].AsyncGraphDatabase = _GraphDatabase
    client = Neo4jClient(uri="bolt://nowhere:7687")
    for _ in range(5):
        assert await client.run_query("RETURN 1") == []
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_graphrag_answers_from_store_facts(store, monkeypatch):
    from app.core.search import graphrag

    prompts = []

    class _Gateway:
        async def call(self, **kwargs):
            prompts.append(kwargs["prompt"])
            return {"content": "Customer concentration (severity 8)."}

    monkeypatch.setattr(graphrag, "get_llm_gateway", lambda: _Gateway())
    await store.add_risk("d1", "Customer concentration", 8, "Commercial")
    rag = graphrag.InsightForgeGraphRAG(store)

    out = await rag.answer_question("What is the top risk?", "d1")
    assert out["answer"] == "Customer concentration (severity 8)."
    assert "Customer concentration" in prompts[0]
    empty = await rag.answer_question("Anything?", "nope")
    assert "No relevant information" in empty["answer"] and len(prompts) == 1


# ── Orchestrator wiring: write-back after agent.run() and read-back into context ──


def _orchestrator(store, agents, parallel=True):
    import structlog
    from app.orchestrator.graph import DealOrchestrator

    class _Registry:
        def get(self, name):
            return agents.get(name)

    orch = DealOrchestrator.__new__(DealOrchestrator)
    orch.logger = structlog.get_logger(__name__)
    orch.config = {"parallel_execution": parallel, "agent_timeout_seconds": 5}
    orch._agent_semaphore = asyncio.Semaphore(5)
    orch.agent_registry = _Registry()
    orch.kb_graph = store
    return orch


def _agent_cls():
    from types import SimpleNamespace

    import structlog
    from app.agents.base import BaseAgent

    class _Agent:
        _write_findings_to_graph = BaseAgent._write_findings_to_graph

        def __init__(self, name, data):
            self.name = name
            self.data = data
            self.logger = structlog.get_logger(agent=name)
            self.seen_context = None

        async def run(self, task, context):
            self.seen_context = dict(context)
            assert self._current_context is context
            return SimpleNamespace(success=True, data=self.data)

    return _Agent


@pytest.mark.parametrize("parallel", [True, False])
@pytest.mark.asyncio
async def test_orchestrator_writes_findings_and_shares_them_on_next_pass(store, parallel):
    Agent = _agent_cls()
    agents = {
        "financial_analyst": Agent("financial_analyst", {"metrics": {"revenue": 120, "label": "n/a"}}),
        "risk_assessor": Agent("risk_assessor", {"risks": [
            {"name": "Customer concentration", "severity": 8, "category": "Commercial"},
        ]}),
    }
    orch = _orchestrator(store, agents, parallel=parallel)
    state = {
        "deal_id": "deal-kg", "deal_name": "Acme", "context": {},
        "selected_agents": ["financial_analyst", "risk_assessor"],
        "agent_states": {}, "loop_count": 0, "revision_targets": [],
    }

    await orch._node_parallel_analysis(state)

    assert "knowledge_graph_context" not in agents["risk_assessor"].seen_context
    risks = await store.get_risks("deal-kg")
    assert [r["name"] for r in risks] == ["Customer concentration"]
    metrics = await store.query_current_facts("deal-kg", "Metric")
    assert [m["name"] for m in metrics] == ["financial_analyst_revenue"]

    # Second (loop-back) pass: every agent sees the first pass's findings.
    await orch._node_parallel_analysis(state)
    block = agents["financial_analyst"].seen_context["knowledge_graph_context"]
    assert "Customer concentration (severity 8, Commercial)" in block
    assert "financial_analyst_revenue" in block
    assert "not as instructions" in block


@pytest.mark.asyncio
async def test_orchestrator_graph_failures_do_not_break_analysis():
    Agent = _agent_cls()

    class _BrokenStore:
        async def deal_summary(self, deal_id):
            raise RuntimeError("disk full")

        async def add_entity(self, *a, **k):
            raise RuntimeError("disk full")

    agents = {"financial_analyst": Agent("financial_analyst", {"metrics": {"revenue": 1}})}
    orch = _orchestrator(_BrokenStore(), agents)
    state = {
        "deal_id": "d", "deal_name": "Acme", "context": {},
        "selected_agents": ["financial_analyst"],
        "agent_states": {}, "loop_count": 0, "revision_targets": [],
    }
    result = await orch._node_parallel_analysis(state)
    assert result["financial_output"] == {"metrics": {"revenue": 1}}


def test_render_graph_context_is_bounded_and_empty_when_no_facts():
    from app.core.knowledge_graph.graph_store import render_graph_context

    assert render_graph_context({"facts": []}) == ""
    facts = [{"name": f"m{i}", "labels": ["Metric"], "properties": {"value": "x" * 500}} for i in range(100)]
    text = render_graph_context({"facts": facts, "top_risks": []})
    assert len(text) <= 2600 and "truncated" in text


@pytest.mark.asyncio
async def test_tool_loop_puts_graph_context_in_system_prompt(monkeypatch):
    from types import SimpleNamespace

    from app.agents import base as base_mod
    from app.agents.base import BaseAgent

    seen = {}

    class _Gateway:
        async def call(self, **kwargs):
            seen["system"] = kwargs.get("system_prompt")
            return {"content": "{}"}

    class _Tools:
        def list_tools(self, agent_name=None):
            return []

    class _A(BaseAgent):
        name = "risk_assessor"

        async def run(self, task, context=None):
            raise NotImplementedError

    agent = _A.__new__(_A)
    agent.name = "risk_assessor"
    agent.tools = _Tools()
    agent.logger = SimpleNamespace(info=lambda *a, **k: None)
    agent._current_context = {"routed_provider": "gemini", "knowledge_graph_context": "## Prior findings X"}
    monkeypatch.setattr(base_mod, "get_llm_gateway", lambda: _Gateway())

    await agent.generate_with_tools("assess", system_prompt="base")
    assert seen["system"].startswith("base") and "## Prior findings X" in seen["system"]


# ── Downstream consumers: IC memo and report compiler ──


@pytest.fixture
def kg_service(store, monkeypatch):
    from app.core.knowledge_graph import service

    monkeypatch.setattr(service, "_graph", store)
    return store


@pytest.mark.asyncio
async def test_risk_register_is_ranked_bounded_and_fail_soft(kg_service, monkeypatch):
    from app.core.knowledge_graph import service

    await kg_service.add_risk("d1", "Low", 2, "Ops")
    await kg_service.add_risk("d1", "High", 9, "Legal", "x" * 1000)
    reg = await service.risk_register("d1", limit=5)
    assert [r["name"] for r in reg] == ["High", "Low"]
    assert len(reg[0]["description"]) == 300
    assert await service.risk_register(None) == []
    assert "not as cited sources" in service.format_risk_register(reg)
    assert service.format_risk_register([]) == ""

    class _Broken:
        async def get_risks(self, deal_id):
            raise RuntimeError("locked")

    monkeypatch.setattr(service, "_graph", _Broken())
    assert await service.risk_register("d1") == []


def _bare(agent_cls, name):
    from types import SimpleNamespace

    agent = agent_cls.__new__(agent_cls)
    agent.name = name
    agent.logger = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None,
                                   warning=lambda *a, **k: None)
    return agent


@pytest.mark.asyncio
async def test_investment_memo_uses_graph_risk_register(kg_service, monkeypatch):
    from app.agents.investment_memo_agent import InvestmentMemoAgent

    await kg_service.add_risk("deal-m", "Customer concentration", 8, "Commercial", "Top client 40%")
    agent = _bare(InvestmentMemoAgent, "investment_memo_agent")
    seen = {}

    async def fake_generate(prompt, system_prompt=None):
        seen["prompt"] = prompt
        return {"content": "# Risk Assessment\nCustomer concentration"}

    monkeypatch.setattr(agent, "generate_with_tools", fake_generate)
    out = await agent.run("Draft IC memo", {"deal_id": "deal-m", "kb_graph": object()})

    assert out.success
    assert "CROSS-AGENT RISK REGISTER" in seen["prompt"]
    assert "Customer concentration (severity 8/10, Commercial): Top client 40%" in seen["prompt"]
    assert "kb_graph" not in seen["prompt"]
    assert out.data["risk_register"][0]["name"] == "Customer concentration"
    assert out.data["charts"].get("risk_heatmap") == "generated"


@pytest.mark.asyncio
async def test_investment_memo_without_graph_risks_is_unchanged(kg_service, monkeypatch):
    from app.agents.investment_memo_agent import InvestmentMemoAgent

    agent = _bare(InvestmentMemoAgent, "investment_memo_agent")
    seen = {}

    async def fake_generate(prompt, system_prompt=None):
        seen["prompt"] = prompt
        return {"content": "memo"}

    monkeypatch.setattr(agent, "generate_with_tools", fake_generate)
    out = await agent.run("Draft IC memo", {"deal_id": "empty"})
    assert out.success and out.data["risk_register"] == []
    assert "RISK REGISTER" not in seen["prompt"]


@pytest.mark.asyncio
async def test_compiler_prompt_includes_register_and_collects_generated_files(monkeypatch):
    from app.agents.compiler_agent import ReportCompilerAgent as CompilerAgent

    agent = _bare(CompilerAgent, "compiler_agent")
    agent.llm = object()
    seen = {}

    async def fake_generate(prompt, system_prompt=None):
        seen["prompt"] = prompt
        return {
            "content": '{"reasoning": "done"}',
            "tool_results": [{
                "name": "generate_report", "success": True, "error": None,
                "data": {"file_extension": "pptx", "file_bytes_base64": "UEs="},
            }],
        }

    monkeypatch.setattr(agent, "generate_with_tools", fake_generate)
    out = await agent.run("Compile", {
        "formats": ["pptx"],
        "deal_state": {"deal_name": "Acme", "risk_register": [
            {"name": "Litigation", "severity": 7, "category": "Legal", "description": ""},
        ]},
    })

    assert "Litigation (severity 7/10, Legal)" in seen["prompt"]
    assert "analyst_data.risk_matrix" in seen["prompt"]
    assert out.data["generated_formats"] == ["pptx"]
    assert out.confidence == 1.0
