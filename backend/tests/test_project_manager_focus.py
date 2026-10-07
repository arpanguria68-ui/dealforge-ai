from types import SimpleNamespace

from app.agents.project_manager import select_tasks_for_focus_mode, wire_stage_dependencies


def _tasks():
    return [
        {"title": f"Task {i}", "assigned_agent": agent, "priority": priority}
        for i, (agent, priority) in enumerate([
            ("financial_analyst", "critical"),
            ("market_researcher", "high"),
            ("risk_assessor", "high"),
            ("legal_advisor", "medium"),
            ("valuation_agent", "critical"),
        ])
    ]


def test_speed_mode_limits_persisted_plan_to_three_relevant_tasks():
    selected = select_tasks_for_focus_mode(_tasks(), "speed")

    assert len(selected) == 3
    assert [task["title"] for task in selected] == ["Task 0", "Task 1", "Task 2"]


def test_balanced_mode_keeps_only_high_and_critical_tasks():
    selected = select_tasks_for_focus_mode(_tasks(), "balanced")

    assert [task["title"] for task in selected] == ["Task 0", "Task 1", "Task 2", "Task 4"]


def test_quality_mode_keeps_the_complete_plan():
    assert select_tasks_for_focus_mode(_tasks(), "quality") == _tasks()


def test_unknown_focus_mode_does_not_silently_drop_work():
    assert select_tasks_for_focus_mode(_tasks(), "unexpected") == _tasks()


def test_explicit_financial_calculations_override_laya_risk_label(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.agents import project_manager as project_manager_module
    from app.core.laya import client as laya_client

    task = (
        "Analyze synthetic company; calculate 2-year revenue CAGR, EBITDA margin, "
        "net debt, EV/EBITDA, and equity value with formulas."
    )

    class Laya:
        async def triage_deal(self, _brief):
            return {
                "track": "risk",
                "track_confidence": 0.99,
                "needs_deep_dive": False,
                "backend": "local",
            }

    class TaskManager:
        async def create_todo_list(self, *, items, **_kwargs):
            items = [
                SimpleNamespace(**item, id=f"task-{index}", depends_on=[])
                for index, item in enumerate(items)
            ]
            return SimpleNamespace(
                id="list-1",
                items=items,
                to_dict=lambda: {"items": [vars(item) for item in items]},
            )

        async def update_task(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(laya_client, "get_laya_client", lambda: Laya())
    monkeypatch.setattr(project_manager_module, "get_task_manager", lambda: TaskManager())
    agent = project_manager_module.ProjectManagerAgent.__new__(project_manager_module.ProjectManagerAgent)

    plan = asyncio.run(agent.generate_plan_with_risks(task, {"deal_id": "test-deal"}))

    assert plan["todo_list"]["items"][0]["assigned_agent"] == "financial_analyst"
    assert plan["laya_decision"]["track"] == "risk"


def test_explicit_multi_workstream_scope_overrides_laya_narrow_classification(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from app.agents import project_manager as project_manager_module
    from app.core.laya import client as laya_client

    task = (
        "Create exactly three workstreams: organize financial records, assess market, "
        "and prepare valuation. Keep missing market data unknown."
    )

    class Laya:
        async def triage_deal(self, _brief):
            return {
                "track": "financial",
                "track_confidence": 0.99,
                "needs_deep_dive": False,
                "backend": "local",
            }

    class TaskManager:
        async def create_todo_list(self, *, items, **_kwargs):
            items = [
                SimpleNamespace(**item, id=f"task-{index}", depends_on=[])
                for index, item in enumerate(items)
            ]
            return SimpleNamespace(
                id="list-1",
                items=items,
                to_dict=lambda: {"items": [vars(item) for item in items]},
            )

        async def update_task(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(laya_client, "get_laya_client", lambda: Laya())
    monkeypatch.setattr(project_manager_module, "get_task_manager", lambda: TaskManager())
    agent = project_manager_module.ProjectManagerAgent.__new__(project_manager_module.ProjectManagerAgent)
    agent.llm = SimpleNamespace(generate=AsyncMock(return_value={
        "content": '{"company_name": "Cedarbrook Pumps", "ticker": ""}'
    }))
    agent._llm_plan = AsyncMock(return_value=[
        {"title": "Financial Records and Gaps", "assigned_agent": "data_curator", "priority": "high"},
        {"title": "Initial valuation", "assigned_agent": "advanced_financial_modeler", "priority": "high"},
    ])

    plan = asyncio.run(agent.generate_plan_with_risks(task, {"deal_id": "test-deal"}))

    assert plan["task_count"] == 3
    assert any(item["assigned_agent"] == "market_researcher" for item in plan["todo_list"]["items"])
    assert plan["laya_decision"]["scope_reduced"] is False
    agent._llm_plan.assert_awaited_once()


def test_numbered_task_lists_are_detected_as_multi_workstream_requests():
    from app.agents.project_manager import ProjectManagerAgent

    assert ProjectManagerAgent._is_multi_workstream_request(
        "Create exactly three tasks: (1) financials, (2) market, (3) valuation."
    )
    assert ProjectManagerAgent._is_multi_workstream_request(
        "Create exactly three independent work items as tasks: financials, market, valuation."
    )


def test_pipeline_orders_curation_decision_and_reporting_after_evidence_stages():
    items = [
        SimpleNamespace(id="fin", assigned_agent="financial_analyst", depends_on=[]),
        SimpleNamespace(id="market", assigned_agent="market_researcher", depends_on=[]),
        SimpleNamespace(id="curate", assigned_agent="data_curator", depends_on=[]),
        SimpleNamespace(id="reason", assigned_agent="complex_reasoning", depends_on=[]),
        SimpleNamespace(id="debate", assigned_agent="debate_moderator", depends_on=[]),
        SimpleNamespace(id="score", assigned_agent="scoring_agent", depends_on=[]),
        SimpleNamespace(id="memo", assigned_agent="investment_memo_agent", depends_on=[]),
    ]

    wire_stage_dependencies(items)
    dependencies = {item.id: set(item.depends_on) for item in items}

    assert dependencies["fin"] == set()
    assert dependencies["market"] == set()
    assert dependencies["curate"] == {"fin", "market"}
    assert dependencies["reason"] == {"fin", "market", "curate"}
    assert dependencies["debate"] == {"fin", "market", "curate", "reason"}
    assert dependencies["score"] == {"fin", "market", "curate", "reason", "debate"}
    assert dependencies["memo"] == {"fin", "market", "curate", "reason", "debate", "score"}


def test_source_data_collection_runs_in_parallel_and_valuation_waits_for_it():
    items = [
        SimpleNamespace(
            id="market",
            title="Market & Industry Research",
            description="Research market size and competitors.",
            assigned_agent="market_researcher",
            depends_on=[],
        ),
        SimpleNamespace(
            id="financial-data",
            title="Target Financial Data Collection",
            description="Collect financial statements and source data for the target.",
            assigned_agent="data_curator",
            depends_on=[],
        ),
        SimpleNamespace(
            id="valuation",
            title="Initial Valuation Framework Development",
            description="Build an initial valuation framework.",
            assigned_agent="advanced_financial_modeler",
            depends_on=[],
        ),
    ]

    wire_stage_dependencies(items)
    dependencies = {item.id: set(item.depends_on) for item in items}

    assert dependencies["market"] == set()
    assert dependencies["financial-data"] == set()
    assert dependencies["valuation"] == {"financial-data"}


def test_collection_semantics_drive_dependencies_even_when_agent_assignment_differs():
    items = [
        SimpleNamespace(
            id="records",
            title="Organize Financial Records and Identify Gaps",
            description="Ingest provided financial statements and debt/cash balances.",
            assigned_agent="financial_analyst",
            depends_on=[],
        ),
        SimpleNamespace(
            id="market",
            title="Assess Industrial Pump Market",
            description="Mark unsupported industry facts unknown.",
            assigned_agent="market_risk_agent",
            depends_on=[],
        ),
        SimpleNamespace(
            id="valuation",
            title="Prepare Initial Valuation Framework",
            description="Build valuation from organized financial records.",
            assigned_agent="valuation_agent",
            depends_on=[],
        ),
    ]

    wire_stage_dependencies(items)
    dependencies = {item.id: set(item.depends_on) for item in items}

    assert dependencies["records"] == set()
    assert dependencies["market"] == set()
    assert dependencies["valuation"] == {"records"}
