"""Dependency scheduling checks using the actual OFAS execution engine."""
from types import SimpleNamespace

import pytest

from app.orchestrator.ofas_engine import OFASExecutionEngine
from app.orchestrator.state import create_ofas_mission, create_ofas_task


class _Agent:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def run(self, _task, _context):
        self.calls += 1
        return SimpleNamespace(data={"value": self.value})


@pytest.mark.asyncio
async def test_independent_tasks_run_in_same_ready_batch():
    mission = create_ofas_mission("eval-1", "EVAL", "test independent work")
    mission["tasks"] = [
        create_ofas_task("a", "A", ["analyst"]),
        create_ofas_task("b", "B", ["analyst"]),
    ]
    agent = _Agent("ok")
    result = await OFASExecutionEngine().execute_ready_tasks(mission, {"analyst": agent})
    assert agent.calls == 2
    assert [task["status"] for task in result["tasks"]] == ["done", "done"]
    assert all(task["outputs"] == {"value": "ok"} for task in result["tasks"])


@pytest.mark.asyncio
async def test_dependent_task_waits_for_prerequisite():
    mission = create_ofas_mission("eval-2", "EVAL", "test dependency")
    mission["tasks"] = [
        create_ofas_task("source", "Source", ["analyst"]),
        create_ofas_task("model", "Model", ["analyst"], dependencies=["source"]),
    ]
    agent = _Agent("ok")
    engine = OFASExecutionEngine()
    await engine.execute_ready_tasks(mission, {"analyst": agent})
    assert mission["tasks"][0]["status"] == "done"
    assert mission["tasks"][1]["status"] == "pending"
    await engine.execute_ready_tasks(mission, {"analyst": agent})
    assert mission["tasks"][1]["status"] == "done"
    assert agent.calls == 2


@pytest.mark.asyncio
async def test_missing_agent_blocks_task_with_diagnostic():
    mission = create_ofas_mission("eval-3", "EVAL", "test missing agent")
    mission["tasks"] = [create_ofas_task("x", "X", ["absent"])]
    await OFASExecutionEngine().execute_ready_tasks(mission, {})
    task = mission["tasks"][0]
    assert task["status"] == "blocked"
    assert "not found in registry" in task["issues"][0]["msg"]
