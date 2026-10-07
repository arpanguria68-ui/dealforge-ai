from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main
from app.core.tasks import task_manager


class FakeTaskList:
    def __init__(self, list_id, status, items):
        self.id = list_id
        self.status = status
        self.items = items

    def to_dict(self):
        states = {"total": len(self.items), "pending": 0, "in_progress": 0, "done": 0}
        for item in self.items:
            if item.status in states:
                states[item.status] += 1
        return {"summary": states}


class FakeStore:
    def __init__(self, deal, activities=None, deals=None):
        self.deal = deal
        self.activities = activities or []
        self.deals = deals or ([deal] if deal else [])

    async def get_deal(self, deal_id):
        return self.deal

    async def get_deal_activity(self, deal_id):
        return self.activities

    async def list_deals(self):
        return self.deals

    async def get_global_activity(self):
        return self.activities

    async def save_deal(self, deal_id, deal):
        self.deal = deal


@pytest.fixture
def patch_stores(monkeypatch):
    def apply(store, lists):
        monkeypatch.setattr(
            main, "RedisStore", SimpleNamespace(get_instance=lambda: store)
        )
        monkeypatch.setattr(
            task_manager, "get_task_manager", lambda: SimpleNamespace(
                get_lists_for_deal=lambda deal_id: _return(lists)
            )
        )
        return store

    return apply


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_status_uses_saved_deal_state_and_task_progress(patch_stores):
    task_list = FakeTaskList(
        "list-1",
        "completed",
        [SimpleNamespace(status="done"), SimpleNamespace(status="done")],
    )
    patch_stores(
        FakeStore({"id": "deal-1", "status": "completed", "current_stage": "completed"}),
        [task_list],
    )

    result = await main.get_deal_status("deal-1")

    assert result["status"] == "completed"
    assert result["current_stage"] == "completed"
    assert result["task_progress"] == {
        "total": 2,
        "pending": 0,
        "in_progress": 0,
        "done": 2,
        "blocked": 0,
    }


@pytest.mark.asyncio
async def test_results_returns_saved_agent_data_without_activity_duplicates(patch_stores):
    financial_data = {
        "valuation": {"dcf_estimate": None},
        "recommendation": "caution",
        "synthesis_status": "deterministic_source_report",
    }
    task = SimpleNamespace(
        id="task-1",
        assigned_agent="financial_analyst",
        title="Focused Financial Assessment",
        status="done",
        result=financial_data,
        updated_at="2026-09-29T10:00:00",
    )
    activity = {
        "agent_type": "financial_analyst",
        "data": financial_data,
        "provider": "lmstudio",
        "confidence": 0.35,
        "reasoning": "Input-grounded",
        "timestamp": "2026-09-29T10:00:00",
    }
    risk_data = {"risk_level": "unknown", "limitations": ["No cash-flow data"]}
    activity_only = {
        "agent_type": "risk_assessor",
        "data": risk_data,
        "provider": "lmstudio",
        "confidence": 0.4,
        "timestamp": "2026-09-29T10:01:00",
    }
    patch_stores(
        FakeStore(
            {
                "id": "deal-1",
                "status": "completed",
                "current_stage": "completed",
                "final_score": None,
                "final_recommendation": "1/1 tasks completed; deal not scored.",
            },
            [activity, activity_only],
        ),
        [FakeTaskList("list-1", "completed", [task])],
    )

    result = await main.get_deal_results("deal-1")

    assert len(result["analyses"]) == 2
    assert result["financial_analysis"] == financial_data
    assert result["risk_assessment"] == risk_data
    assert result["final_score"] is None
    assert result["recommendation"] == "1/1 tasks completed; deal not scored."
    assert result["analyses"][0]["provider"] == "lmstudio"
    assert result["analyses"][0]["confidence"] is None
    assert result["task_progress"]["done"] == 1


@pytest.mark.asyncio
async def test_status_and_results_return_404_for_missing_deal(patch_stores):
    patch_stores(FakeStore(None), [])

    with pytest.raises(HTTPException) as status_error:
        await main.get_deal_status("missing")
    with pytest.raises(HTTPException) as results_error:
        await main.get_deal_results("missing")

    assert status_error.value.status_code == 404
    assert results_error.value.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_confidence_is_not_derived_from_deal_score(monkeypatch):
    store = FakeStore(
        None,
        deals=[
            {
                "id": "scored",
                "status": "completed",
                "final_score": 0.8,
                "_confidence_scores": {"financial_analyst": 0.35},
            },
            {
                "id": "unscored",
                "status": "completed",
                "final_score": None,
                "_confidence_scores": {"risk_assessor": 0.55},
            },
            {
                "id": "active",
                "status": "running",
                "final_score": None,
                "_confidence_scores": {"financial_analyst": 1.0},
            },
        ],
    )
    monkeypatch.setattr(
        main, "RedisStore", SimpleNamespace(get_instance=lambda: store)
    )

    result = await main.dashboard_metrics()

    assert result["avg_confidence"] == 45.0
    assert result["high_risk_alerts"] == 0
    assert result["completed_deals"] == 2


@pytest.mark.asyncio
async def test_dashboard_excludes_uncalibrated_source_retrieval_confidence(monkeypatch):
    store = FakeStore(
        None,
        activities=[
            {
                "deal_id": "sec-only",
                "agent_type": "financial_analyst",
                "confidence": 0.65,
                "data": {"synthesis_status": "deterministic_source_report"},
            }
        ],
        deals=[
            {
                "id": "sec-only",
                "status": "completed",
                "_confidence_scores": {"financial_analyst": 0.65},
            }
        ],
    )
    monkeypatch.setattr(main, "RedisStore", SimpleNamespace(get_instance=lambda: store))

    result = await main.dashboard_metrics()

    assert result["avg_confidence"] is None


@pytest.mark.asyncio
async def test_deal_updates_emit_timezone_aware_utc_timestamps(monkeypatch):
    store = FakeStore({"id": "deal-1", "status": "running"})
    monkeypatch.setattr(main, "RedisStore", SimpleNamespace(get_instance=lambda: store))
    request = SimpleNamespace(json=lambda: _return({"current_stage": "completed"}))

    result = await main.update_deal("deal-1", request)

    assert result["updated_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_deal_cannot_be_marked_completed_while_saved_tasks_are_pending(monkeypatch):
    store = FakeStore({"id": "deal-1", "status": "running"})
    pending = SimpleNamespace(status="pending")
    monkeypatch.setattr(main, "RedisStore", SimpleNamespace(get_instance=lambda: store))
    monkeypatch.setattr(
        task_manager,
        "get_task_manager",
        lambda: SimpleNamespace(get_lists_for_deal=lambda _deal_id: _return([
            FakeTaskList("list-1", "in_progress", [pending]),
        ])),
    )
    request = SimpleNamespace(json=lambda: _return({"status": "completed"}))

    with pytest.raises(HTTPException) as exc:
        await main.update_deal("deal-1", request)

    assert exc.value.status_code == 409
    assert store.deal["status"] == "running"


@pytest.mark.asyncio
async def test_agent_activity_alone_never_completes_a_deal(monkeypatch):
    class ActivityStore:
        def __init__(self):
            self.deal = {"id": "deal-1", "status": "running", "current_stage": "analysis"}
            self.events = []

        async def add_activity(self, event):
            self.events.append(event)

        async def get_deal(self, _deal_id):
            return self.deal

        async def save_deal(self, _deal_id, deal):
            self.deal = deal

    store = ActivityStore()

    class DocumentStore:
        async def invalidate(self, _deal_id):
            return None

    monkeypatch.setattr(main, "RedisStore", SimpleNamespace(get_instance=lambda: store))
    monkeypatch.setattr(
        "app.core.document_store.DocumentStore",
        SimpleNamespace(get_instance=lambda: DocumentStore()),
    )

    for index, agent_name in enumerate(("financial_analyst", "risk_assessor", "legal_advisor", "market_researcher")):
        await main.log_agent_activity({
            "deal_id": "deal-1", "agent_type": agent_name,
            "data": {"finding": f"{agent_name} completed"},
            **({"final_score": 0.8} if index == 3 else {}),
        })

    assert len(store.events) == 4
    assert store.deal["status"] == "running"
