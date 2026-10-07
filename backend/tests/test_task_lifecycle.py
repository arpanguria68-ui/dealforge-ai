import pytest


@pytest.mark.asyncio
async def test_last_persisted_done_task_completes_list_and_status_close_is_idempotent(tmp_path):
    from app.core.tasks.task_manager import TaskManager

    manager = TaskManager(db_path=str(tmp_path / "tasks.db"))
    todo = await manager.create_todo_list(
        deal_id="deal-test",
        title="Test analysis",
        items=[{"title": "Financial review", "assigned_agent": "financial_analyst"}],
    )
    await manager.approve_list(todo.id)
    await manager.set_list_status(todo.id, "in_progress")

    await manager.update_task(
        todo.id,
        todo.items[0].id,
        {"status": "done", "result": {"success": True}},
    )

    persisted = await manager.get_todo_list(todo.id)
    assert persisted.status == "completed"
    assert persisted.items[0].status == "done"
    assert (await manager.set_list_status(todo.id, "completed")).status == "completed"


@pytest.mark.asyncio
async def test_manual_execution_refuses_task_until_prerequisites_are_done(tmp_path, monkeypatch):
    from app.agents import project_manager
    from app.core.tasks.task_manager import TaskManager

    manager = TaskManager(db_path=str(tmp_path / "tasks.db"))
    todo = await manager.create_todo_list(
        deal_id="deal-test",
        title="Dependency test",
        items=[
            {"title": "Evidence gathering", "assigned_agent": "financial_analyst"},
            {"title": "Curation", "assigned_agent": "data_curator"},
        ],
    )
    first, second = todo.items
    await manager.update_task(todo.id, second.id, {"depends_on": [first.id]})
    await manager.approve_list(todo.id)
    monkeypatch.setattr(project_manager, "get_task_manager", lambda: manager)

    class Registry:
        def get(self, _):
            raise AssertionError("A blocked task must not invoke its agent")

    result = await project_manager.ProjectManagerAgent.__new__(
        project_manager.ProjectManagerAgent
    ).execute_task(todo.id, second.id, Registry())

    assert result == {"error": "dependencies_not_satisfied", "blocked_by": [first.id]}
    assert second.status == "pending"
