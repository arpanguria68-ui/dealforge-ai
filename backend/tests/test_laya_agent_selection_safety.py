import pytest
import asyncio
from types import SimpleNamespace


class _Registry:
    def list_agents(self):
        return [
            "financial_analyst",
            "legal_advisor",
            "risk_assessor",
            "market_researcher",
            "ai_tech_diligence_agent",
            "esg_agent",
        ]


class _Laya:
    def __init__(self, triage):
        self.triage = triage

    async def triage_deal(self, _brief):
        return self.triage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "triage",
    [
        {"track": "financial", "track_confidence": 0.99, "needs_deep_dive": False, "backend": "local"},
        {"track": "esg", "track_confidence": 0.99, "needs_deep_dive": False, "backend": "local"},
        {"track": "tech", "track_confidence": 0.99, "needs_deep_dive": True, "backend": "lmstudio"},
        {"track": "tech", "track_confidence": 0.70, "needs_deep_dive": True, "backend": "local"},
    ],
)
async def test_laya_never_removes_core_diligence_agents(monkeypatch, triage):
    from app.agents import base
    from app.core.laya import client as laya_client
    from app.orchestrator.planner import AgentSelectionPlanner

    monkeypatch.setattr(base, "get_agent_registry", lambda: _Registry())
    monkeypatch.setattr(laya_client, "get_laya_client", lambda: _Laya(triage))

    selected = await AgentSelectionPlanner().plan_execution(
        {"context": {"deal_brief": "Review the company and transaction."}}
    )

    assert selected[:4] == [
        "financial_analyst",
        "legal_advisor",
        "risk_assessor",
        "market_researcher",
    ]


@pytest.mark.asyncio
async def test_confident_laya_deep_dive_adds_relevant_specialist(monkeypatch):
    from app.agents import base
    from app.core.laya import client as laya_client
    from app.orchestrator.planner import AgentSelectionPlanner

    monkeypatch.setattr(base, "get_agent_registry", lambda: _Registry())
    monkeypatch.setattr(
        laya_client,
        "get_laya_client",
        lambda: _Laya({
            "track": "esg",
            "track_confidence": 0.93,
            "needs_deep_dive": True,
            "backend": "local",
        }),
    )

    selected = await AgentSelectionPlanner().plan_execution(
        {"context": {"deal_brief": "Assess ESG exposure in this transaction."}}
    )

    assert selected == [
        "financial_analyst",
        "legal_advisor",
        "risk_assessor",
        "market_researcher",
        "esg_agent",
    ]


@pytest.mark.asyncio
async def test_selected_specialist_runs_with_task_and_is_saved_for_synthesis():
    from app.orchestrator.graph import DealOrchestrator

    class _Agent:
        def __init__(self, name):
            self.name = name
            self.task = None

        async def run(self, task, context):
            self.task = task
            return SimpleNamespace(success=True, data={"finding": f"{self.name} result"})

    class _ExecutionRegistry:
        def __init__(self):
            self.agents = {name: _Agent(name) for name in (
                "financial_analyst", "legal_advisor", "risk_assessor",
                "market_researcher", "ai_tech_diligence_agent",
            )}

        def get(self, name):
            return self.agents.get(name)

    orchestrator = DealOrchestrator.__new__(DealOrchestrator)
    orchestrator.logger = __import__("structlog").get_logger(__name__)
    orchestrator.config = {"parallel_execution": True, "agent_timeout_seconds": 5}
    orchestrator._agent_semaphore = asyncio.Semaphore(5)
    orchestrator.agent_registry = _ExecutionRegistry()
    orchestrator.kb_graph = object()

    state = {
        "deal_id": "deal-test",
        "deal_name": "TestCo",
        "context": {},
        "selected_agents": [
            "financial_analyst", "legal_advisor", "risk_assessor",
            "market_researcher", "ai_tech_diligence_agent",
        ],
        "dynamic_tasks": {"financial_analyst": "Validate the supplied cash-flow bridge."},
        "agent_states": {},
        "loop_count": 0,
        "revision_targets": [],
    }

    result = await orchestrator._node_parallel_analysis(state)

    agents = orchestrator.agent_registry.agents
    assert agents["financial_analyst"].task == "Validate the supplied cash-flow bridge."
    assert "focused ai tech diligence" in agents["ai_tech_diligence_agent"].task.lower()
    assert result["specialist_outputs"] == {
        "ai_tech_diligence_agent": {"finding": "ai_tech_diligence_agent result"}
    }
