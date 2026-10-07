from types import SimpleNamespace

import pytest

from app import main
from app.agents.risk_assessor import MarketRiskAgent


@pytest.mark.asyncio
async def test_chat_execute_task_uses_approved_task_list_guard(monkeypatch):
    planned_task = SimpleNamespace(id="task-1", depends_on=[], status="pending")
    todo = SimpleNamespace(
        id="list-1", deal_id="deal-1", status="approved", items=[planned_task]
    )

    class TaskManager:
        async def get_todo_list(self, list_id):
            assert list_id == "list-1"
            return todo

    class Agent:
        llm = None
        _current_context = None

        def is_input_only_request(self, _task):
            return False

        async def run(self, task, context=None):
            self._current_context = context
            return SimpleNamespace(
                success=True,
                data={"finding": "grounded"},
                reasoning="Completed",
                confidence=0.5,
                execution_time_ms=12,
            )

    class Registry:
        def get(self, _agent_type):
            return Agent()

    class Router:
        async def get_provider_for_text(self, _agent_type, _task):
            return "gemini", False

    class Request:
        async def json(self):
            return {
                "agent_type": "financial_analyst",
                "task": "Review Microsoft financials",
                "deal_id": "deal-1",
                "task_id": "task-1",
                "task_list_id": "list-1",
                "ticker": "MSFT",
                "company_name": "Microsoft Corporation",
                "user_prompt": "Use the supplied FY2025 revenue of $10 million.",
            }

    monkeypatch.setattr(main, "get_task_manager", lambda: TaskManager())
    monkeypatch.setattr("app.agents.base.get_agent_registry", lambda: Registry())
    monkeypatch.setattr("app.core.llm.model_router.get_model_router", lambda: Router())
    monkeypatch.setattr("app.core.llm.get_llm_client", lambda _provider: object())

    response = await main.chat_execute_task(Request())

    assert response["success"] is True
    assert response["data"] == {"finding": "grounded"}
    assert response["provider"] == "gemini"


@pytest.mark.asyncio
async def test_execute_task_propagates_original_user_prompt(monkeypatch):
    class Agent:
        llm = None

        def is_input_only_request(self, _task):
            return False

        async def run_with_structure(self, _task, context=None):
            self.captured_context = context
            return SimpleNamespace(
                success=True, data={}, reasoning="ok", confidence=0.5, execution_time_ms=1
            )

    agent = Agent()

    class Registry:
        def get(self, _agent_type):
            return agent

    class Router:
        async def get_provider_for_text(self, _agent_type, _task):
            return "gemini", False

    class Request:
        async def json(self):
            return {
                "agent_type": "business_analyst",
                "task": "Assess supplied company figures",
                "user_prompt": "FY2025 revenue was $10 million.",
            }

    monkeypatch.setattr("app.agents.base.get_agent_registry", lambda: Registry())
    monkeypatch.setattr("app.core.llm.model_router.get_model_router", lambda: Router())
    monkeypatch.setattr("app.core.llm.get_llm_client", lambda _provider: object())

    await main.chat_execute_task(Request())

    assert agent.captured_context["user_prompt"] == "FY2025 revenue was $10 million."


@pytest.mark.asyncio
async def test_market_risk_does_not_invent_industry_when_missing():
    agent = MarketRiskAgent.__new__(MarketRiskAgent)

    result = await agent.run("Assess market risks", context={})

    assert result.success is True
    assert result.data["market_context"] == {
        "industry": None,
        "tam": None,
        "competitor_count": None,
        "status": "insufficient_evidence",
    }
