from app.core.reports.analysis_export import build_analysis_export_payload


def test_json_export_uses_persisted_results_and_preserves_unknowns():
    payload = build_analysis_export_payload(
        {"id": "deal-1", "target_company": "Acme", "final_score": None},
        {
            "id": "list-1", "title": "Acme assessment", "status": "completed", "company_name": "Acme",
            "items": [{
                "id": "task-1", "title": "Financial analysis", "assigned_agent": "financial_analyst",
                "status": "done", "updated_at": "2026-09-30T12:00:00",
                "result": {"historical_financials": [{"period": "2025", "revenue": 100}],
                           "valuation": {"dcf_estimate": None}, "sources": [{"url": "https://example.test"}]},
            }],
        },
        exported_at="2026-10-01T00:00:00+00:00",
    )

    analysis = payload["analysis"]
    exported_result = analysis["agents"][0]["result"]
    assert payload["schema_version"] == "1.1.0"
    assert payload["exported_at"] == "2026-10-01T00:00:00+00:00"
    assert analysis["completed_at"] == "2026-09-30T12:00:00"
    assert analysis["completed_tasks"] == 1
    assert exported_result["valuation"]["dcf_estimate"] is None
    assert exported_result["sources"][0]["url"] == "https://example.test"
