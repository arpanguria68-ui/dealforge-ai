"""Data truncation, API input limits and tool-argument handling."""

import json

import pytest
from fastapi.testclient import TestClient


# ── Prompt context rendering ──────────────────────────────────────────


def test_render_context_keeps_deal_facts_and_valid_json():
    from app.core.prompt_context import render_context

    ctx = {
        "deal_id": "d1",
        "deal_brief": "Acme is a vertical SaaS vendor. " * 200,
        "laya_triage": {"track": "financial"},
        "kb_graph": object(),
        "knowledge_graph_context": "## Prior findings\n" + "- risk\n" * 500,
        "skill_context": "x" * 5000,
        "fact_base": {"company_name": "Acme", "metrics": {"revenue": 120.5, "ebitda": 30.1}},
        "financial_data": {"revenue": [100, 110, 120.5]},
    }
    old = json.dumps(ctx, default=str)[:2000]
    assert '"fact_base"' not in old, "documents the old bug this replaces"

    out = render_context(ctx, budget=2000)
    parsed = json.loads(out)  # always valid JSON
    assert parsed["fact_base"]["metrics"]["revenue"] == 120.5
    assert parsed["financial_data"] == {"revenue": [100, 110, 120.5]}
    assert "kb_graph" not in parsed and "knowledge_graph_context" not in parsed
    assert "skill_context" not in parsed and "laya_triage" not in parsed
    assert len(out) <= 2100


def test_render_context_marks_truncation_and_omissions():
    from app.core.prompt_context import render_context

    ctx = {"target_company": "Acme", "notes": "n" * 10_000, "big_list": list(range(5000)),
           "extra_1": "y" * 3000, "extra_2": "z" * 3000}
    parsed = json.loads(render_context(ctx, budget=4000))
    assert parsed["target_company"] == "Acme"
    assert "truncated" in parsed["notes"]
    assert any("more items truncated" in str(x) for x in parsed.get("big_list", [])) \
        or "big_list" in parsed.get("_omitted_for_length", [])
    assert parsed.get("_omitted_for_length"), "keys that did not fit are listed, not silently dropped"


# ── Retrieval excerpts and ingestion caps ─────────────────────────────


def test_search_excerpt_centres_on_matched_terms():
    from app.core.memory.local_pageindex import LocalPageIndexService

    text = ("Boilerplate intro. " * 300) + "Customer churn rose to 18% in FY2024 due to pricing." + (" Filler." * 300)
    excerpt = LocalPageIndexService._excerpt(text, {"churn", "fy2024"}, 2000)
    assert "Customer churn rose to 18%" in excerpt
    assert len(excerpt) <= 2010 and excerpt.startswith("… ")
    assert LocalPageIndexService._excerpt("short", {"x"}, 2000) == "short"


def test_ingestion_cap_is_large_explicit_and_logged(monkeypatch):
    from app.core.tasks import knowledge_ingestion as ki

    monkeypatch.setattr(ki, "MAX_INGEST_CHARS", 100)
    capped = ki._cap("a" * 250, "big.pdf")
    assert capped.startswith("a" * 100) and "[TRUNCATED: 150 chars not indexed]" in capped
    assert ki._cap("small", "s.pdf") == "small"
    assert ki.MAX_PDF_PAGES >= 1000 and ki.MAX_SHEET_ROWS >= 5000


# ── API input limits ──────────────────────────────────────────────────


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_upload_rejects_unsupported_types_oversize_and_empty(client, monkeypatch):
    import app.main as main

    res = client.post("/api/v1/documents/upload", files={"file": ("run.exe", b"MZ...", "application/octet-stream")})
    assert res.status_code == 415

    monkeypatch.setattr(main, "UPLOAD_MAX_BYTES", 1024)
    res = client.post("/api/v1/documents/upload", files={"file": ("big.txt", b"x" * 4096, "text/plain")})
    assert res.status_code == 413

    res = client.post("/api/v1/documents/upload", files={"file": ("empty.txt", b"", "text/plain")})
    assert res.status_code == 400


def test_bulk_upload_reports_per_file_limit_errors(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "UPLOAD_MAX_FILES", 1)
    res = client.post("/api/v1/documents/upload/bulk", files=[
        ("files", ("a.txt", b"a", "text/plain")), ("files", ("b.txt", b"b", "text/plain")),
    ])
    assert res.status_code == 413


def test_json_body_size_limit(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "API_MAX_JSON_BYTES", 100)
    res = client.post("/api/v1/agent-activity", json={"blob": "x" * 500})
    assert res.status_code == 413


def test_global_activity_stores_slim_events():
    from app.core.redis_store import _slim_event

    evt = {"deal_id": "d", "agent_type": "compiler_agent", "reasoning": "r" * 2000,
           "data": {"files_base64": {"pptx": "UEs" * 100_000}, "synthesis_status": "deterministic_source_report"}}
    slim = _slim_event(evt)
    assert slim["data"] == {"synthesis_status": "deterministic_source_report"}
    assert len(json.dumps(slim)) < 1000


# ── Tool argument types ───────────────────────────────────────────────


def test_tool_arguments_are_coerced_or_rejected_with_actionable_errors():
    from app.core.tools.tool_router import ToolRouter

    schema = {"type": "object", "properties": {
        "n": {"type": "integer"}, "x": {"type": "number"}, "flag": {"type": "boolean"},
        "items": {"type": "array"}, "opts": {"type": "object"}, "name": {"type": "string"},
        "sector": {"type": "string", "enum": ["technology", "healthcare"]},
    }}
    params = {"n": "1,000", "x": "2.5", "flag": "true", "items": "[1, 2]", "opts": '{"a": 1}',
              "name": 42, "sector": "technology"}
    assert ToolRouter._coerce_param_types(schema, params) is None
    assert params == {"n": 1000, "x": 2.5, "flag": True, "items": [1, 2], "opts": {"a": 1},
                      "name": "42", "sector": "technology"}

    bad = {"n": "lots", "items": "not json", "sector": "alien_tech"}
    err = ToolRouter._coerce_param_types(schema, bad)
    assert "n must be integer" in err and "items must be array" in err and "sector must be one of" in err


@pytest.mark.asyncio
async def test_router_returns_type_errors_to_the_model():
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter()
    router.register_default_tools(object())
    res = await router.execute("churn_monte_carlo", {"base_count": "five hundred", "cultural_fit_score": "40"})
    assert not res.success and "base_count must be number" in res.error
    ok = await router.execute("churn_monte_carlo", {"base_count": "500", "cultural_fit_score": "40"})
    assert ok.success
