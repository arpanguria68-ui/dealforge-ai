"""Supervisor planning/status contract tests; no LLM or external service needed."""
import pytest

from app.agents.ofas_supervisor import OFASSupervisorAgent


@pytest.mark.asyncio
async def test_supervisor_plans_mission_with_raci_and_ready_tasks():
    supervisor = OFASSupervisorAgent(llm_client=object())
    result = await supervisor.run(
        "Assess the target",
        context={"action": "plan_mission", "deal_id": "eval-deal", "ticker": "EVAL"},
    )
    assert result.success
    mission = result.data["mission"]
    assert mission["deal_id"] == "eval-deal"
    assert result.data["task_count"] == len(mission["tasks"]) > 0
    assert result.data["ready_tasks"]
    assert all("R" in task["raci"] and "A" in task["raci"] for task in mission["tasks"])


@pytest.mark.asyncio
async def test_supervisor_rejects_unknown_action_without_crashing():
    result = await OFASSupervisorAgent(llm_client=object()).run(
        "noop", context={"action": "not-supported"}
    )
    assert not result.success
    assert result.data["error"] == "Unknown action: not-supported"
