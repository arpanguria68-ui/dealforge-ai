"""Stable, server-side export of the persisted analysis record."""

from datetime import datetime, timezone
from typing import Any


def build_analysis_export_payload(deal: dict, todo_list: dict, exported_at: str | None = None) -> dict:
    items = todo_list.get("items") or []
    done_items = [item for item in items if item.get("status") == "done"]
    completed_at = max(
        (item.get("updated_at") for item in done_items if item.get("updated_at")),
        default=None,
    )
    company = deal.get("target_company") or todo_list.get("company_name") or None
    return {
        "schema_version": "1.1.0",
        "exported_at": exported_at or datetime.now(timezone.utc).isoformat(),
        "deal": {
            "id": deal.get("id"),
            "name": deal.get("name") or todo_list.get("title") or "Deal analysis",
            "target_company": company,
            "industry": deal.get("industry"),
            "status": deal.get("status") or todo_list.get("status"),
            "current_stage": deal.get("current_stage"),
        },
        "analysis": {
            "task_list_id": todo_list.get("id"),
            "status": todo_list.get("status"),
            "completed_at": completed_at,
            "final_score": deal.get("final_score"),
            "recommendation": deal.get("final_recommendation"),
            "completed_tasks": len(done_items),
            "total_tasks": len(items),
            "agents": [
                {
                    "task_id": item.get("id"),
                    "task": item.get("title"),
                    "agent": item.get("assigned_agent") or None,
                    "status": item.get("status", "unknown"),
                    "result": item.get("result"),
                    "updated_at": item.get("updated_at"),
                }
                for item in items
            ],
        },
        "limitations": (
            "Results reflect only the inputs and sources recorded by the analysis. "
            "Null or absent values were not established; do not treat them as zero or as verified facts."
        ),
    }
