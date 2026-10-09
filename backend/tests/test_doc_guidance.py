"""Guidance layer: what to ask an LLM for, and how its response becomes a validated document."""

import base64
import json

import pytest

from app.core.reports import doc_guidance as dg
from app.core.reports.document_workflow import DOC_TYPES
from app.core.reports.report_guardrails import ReportGuardrails

DEAL = {"id": "deal-g", "name": "Project Acme", "target_company": "Acme SaaS", "status": "new"}

GOOD = {
    "title": "Acme IC Memo",
    "sections": {
        "executive_summary": {"paragraphs": ["Acme is a profitable SaaS vendor.", "We recommend proceeding."], "sources": ["S1"]},
        "investment_thesis": ["Sticky customers [S1]", "Expanding margins [S1]"],
        "financial_metrics": {"table": {"columns": ["Metric", "Period", "Value", "Source"],
                                        "rows": [["Revenue", "FY2024", "$118M", "S1"]]}, "sources": ["S1"]},
        "risk_assessment": {"table": {"columns": ["Risk", "Severity", "Category", "Mitigation"],
                                      "rows": [["Customer concentration", "7", "Commercial", "Diversify"]]}},
    },
    "sources": [{"id": "S1", "title": "Acme 10-K", "url": "https://sec.gov/acme"}],
}


@pytest.mark.parametrize("doc_type", sorted(DOC_TYPES))
def test_guidance_covers_every_document_type(doc_type):
    guide = dg.guide_for(doc_type)
    assert guide["sections"] and all(s["purpose"] and s["guidance"] for s in guide["sections"])
    required = [s["key"] for s in guide["sections"] if s["required"]]
    assert set(required) == set(DOC_TYPES[doc_type]["required"])
    schema = dg.response_schema(doc_type)
    assert set(schema["properties"]["sections"]["required"]) == set(required)
    json.dumps(schema)  # serialisable for structured-output APIs
    prompt = dg.llm_instructions(doc_type)
    assert "ONE JSON object" in prompt and all(k in prompt for k in required)


def test_unknown_doc_type_is_rejected():
    with pytest.raises(dg.ContentError):
        dg.guide_for("whitepaper")


@pytest.mark.parametrize("raw", [
    json.dumps(GOOD),
    "```json\n" + json.dumps(GOOD) + "\n```",
    "Sure! Here is the memo:\n" + json.dumps(GOOD) + "\nLet me know if you need changes.",
    GOOD,
])
def test_parser_accepts_the_shapes_models_actually_return(raw):
    assert dg.parse_llm_response(raw)["title"] == "Acme IC Memo"


@pytest.mark.parametrize("raw", ["", "no json here", '{"sections": ', "[1, 2]", None])
def test_parser_rejects_unusable_responses(raw):
    with pytest.raises(dg.ContentError):
        dg.parse_llm_response(raw)


def test_valid_content_becomes_a_review_required_model():
    result = dg.validate_content("ic_memo", GOOD, deal=DEAL)
    assert result.ok, result.issues
    model = result.build_model()
    assert model["review_status"] == "review_required"
    assert "written by an LLM" in model["warnings"][0]
    keys = [s["key"] for s in model["sections"]]
    assert keys == ["executive_summary", "investment_thesis", "financial_metrics", "risk_assessment", "sources_methodology"]
    assert model["subtitle"] == "Acme SaaS"
    sources = next(s for s in model["sections"] if s["key"] == "sources_methodology")
    assert "[S1] Acme 10-K (https://sec.gov/acme)" in sources["blocks"][0]["items"]


def test_lenient_shapes_are_normalised():
    result = dg.validate_content("ic_memo", {"sections": {
        "executive_summary": "First paragraph.\n\nSecond paragraph.",
        "investment_thesis": "- one\n- two\n3. three",
        "financial_metrics": {"table": {"columns": ["Metric", "Value"], "rows": [["Revenue"]]}},
        "risk_assessment": ["Concentration"],
    }})
    assert result.ok, result.issues
    by_key = {s["key"]: s["blocks"] for s in result.sections}
    assert [b["text"] for b in by_key["executive_summary"]] == ["First paragraph.", "Second paragraph."]
    assert by_key["investment_thesis"][0]["items"] == ["one", "two", "three"]
    assert by_key["financial_metrics"][0]["rows"] == [["Revenue", ""]]  # short rows are padded, not dropped


def test_missing_and_empty_required_sections_are_errors():
    result = dg.validate_content("ic_memo", {"sections": {"executive_summary": "  ", "risk_assessment": "x"}})
    assert not result.ok
    problems = {(i["section"], i["message"]) for i in result.errors}
    assert ("executive_summary", "Required section is empty.") in problems
    assert ("investment_thesis", "Required section is missing.") in problems
    assert ("financial_metrics", "Required section is missing.") in problems


def test_bad_tables_are_errors_not_silently_fixed():
    result = dg.validate_content("ic_memo", {"sections": {
        **GOOD["sections"],
        "financial_metrics": {"table": {"columns": ["Metric"], "rows": [["Revenue", "too", "wide"]]}},
    }})
    assert not result.ok
    assert any("does not fit" in i["message"] for i in result.errors)
    assert not dg.validate_content("ic_memo", {"sections": {**GOOD["sections"], "financial_metrics": {"table": "nope"}}}).ok


def test_warnings_do_not_block_but_are_carried_into_the_document():
    sections = dict(GOOD["sections"])
    sections["market_analysis"] = ["Market grows 25% a year"]            # figure, no source
    sections["appendix"] = "not part of an IC memo"                      # unknown section
    sections["executive_summary"] = {"paragraphs": ["word " * 400], "sources": ["S9"]}  # too long + unknown source
    result = dg.validate_content("ic_memo", {**GOOD, "sections": sections})
    assert result.ok
    messages = " | ".join(f"{i['section']}: {i['message']}" for i in result.issues if i["level"] == "warning")
    assert "appendix: Not part of this document type" in messages
    assert "market_analysis: States figures but cites no sources" in messages
    assert "executive_summary: 400 words exceeds" in messages
    assert "S9" in messages
    # Nothing was truncated: the long paragraph is intact in the model.
    summary = next(s for s in result.build_model()["sections"] if s["key"] == "executive_summary")
    assert len(summary["blocks"][0]["text"].split()) == 400
    assert any("appendix" in w for w in result.build_model()["warnings"])


def test_control_characters_are_stripped():
    result = dg.validate_content("one_pager", {"sections": {"snapshot": {"table": {
        "columns": ["Item", "Value"], "rows": [["Target\x00", "Acme\x07"]]}}}})
    assert result.ok and result.sections[0]["blocks"][0]["rows"] == [["Target", "Acme"]]


def test_oversized_content_is_refused():
    result = dg.validate_content("ic_memo", "{" + '"x": "' + "a" * (dg.MAX_CONTENT_BYTES + 1) + '"}')
    assert not result.ok and "larger than" in result.errors[0]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type,formats", [
    ("ic_memo", ["docx", "pdf"]), ("board_deck", ["pptx", "pdf"]), ("risk_report", ["docx", "xlsx"]),
])
async def test_llm_content_renders_to_real_files(doc_type, formats):
    from app.core.reports.document_workflow import render_formats

    content = {"sections": {
        **GOOD["sections"],
        "snapshot": {"table": {"columns": ["Item", "Value"], "rows": [["Target", "Acme"]]}},
    }, "sources": GOOD["sources"]}
    result = dg.validate_content(doc_type, content, deal=DEAL)
    assert result.ok, result.issues
    artifacts, errors = await render_formats(result.build_model(), formats)
    assert not errors and set(artifacts) == set(formats)
    for fmt, blob in artifacts.items():
        assert ReportGuardrails.deep_validate(fmt, blob)["valid"], fmt


# ── tool + endpoint entry points ───────────────────────────────────────

@pytest.fixture(scope="module")
def router():
    from app.core.tools.tool_router import ToolRouter

    r = ToolRouter()
    r.register_default_tools(object())
    return r


@pytest.mark.asyncio
async def test_build_document_tool_accepts_llm_content(router):
    res = await router.execute("build_document", {"doc_type": "ic_memo", "content": GOOD, "deal": DEAL,
                                                  "formats": ["docx"]})
    assert res.success, res.error
    assert res.data["content_source"] == "llm_supplied" and res.data["review_status"] == "review_required"
    assert ReportGuardrails.deep_validate("docx", base64.b64decode(res.data["files_base64"]["docx"]))["valid"]


@pytest.mark.asyncio
async def test_build_document_tool_reports_invalid_content_precisely(router):
    res = await router.execute("build_document", {"doc_type": "ic_memo", "content": {"sections": {}}})
    assert not res.success and "Required section is missing" in res.error
    assert any(i["section"] == "executive_summary" for i in res.data["issues"])
    assert not (await router.execute("build_document", {"content": GOOD})).success  # doc_type required


class _FakeRedis:
    async def get_deal(self, deal_id):
        return DEAL if deal_id == "deal-g" else None


class _FakeDocStore:
    def __init__(self):
        self.published = None

    async def replace_documents(self, deal_id, artifacts, metadata):
        self.published = (artifacts, metadata)

    async def list_documents(self, deal_id):
        return [] if not self.published else [{"format": f} for f in self.published[0]]


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main
    from app.core.document_store import DocumentStore
    from app.core.redis_store import RedisStore

    store = _FakeDocStore()
    monkeypatch.setattr(RedisStore, "get_instance", classmethod(lambda cls: _FakeRedis()))
    monkeypatch.setattr(DocumentStore, "get_instance", classmethod(lambda cls: store))
    return TestClient(main.app), store


def test_guidance_endpoint(api):
    client, _ = api
    body = client.get("/api/v1/documents/guidance/ic_memo").json()
    assert body["guide"]["doc_type"] == "ic_memo" and "response_schema" in body and "ONE JSON object" in body["instructions"]
    assert client.get("/api/v1/documents/guidance/nope").status_code == 404


def test_from_content_publishes_a_pending_review_bundle(api):
    client, store = api
    res = client.post("/api/v1/deals/deal-g/documents/from-content",
                      json={"doc_type": "ic_memo", "content": GOOD, "formats": ["docx", "pdf"]})
    body = res.json()
    assert res.status_code == 200 and body["status"] == "complete", body
    artifacts, metadata = store.published
    assert set(artifacts) == {"docx", "pdf"}
    assert metadata["release_status"] == "pending_review" and metadata["content_source"] == "llm_supplied"
    assert metadata["review_status"] == "review_required"


def test_from_content_rejects_invalid_content_without_publishing(api):
    client, store = api
    res = client.post("/api/v1/deals/deal-g/documents/from-content",
                      json={"doc_type": "ic_memo", "content": '{"sections": {"executive_summary": "only this"}}'})
    body = res.json()
    assert res.status_code == 200 and body["status"] == "rejected" and not body["ok"]
    assert any(i["section"] == "risk_assessment" for i in body["issues"])
    assert store.published is None


def test_from_content_dry_run_and_input_errors(api):
    client, store = api
    dry = client.post("/api/v1/deals/deal-g/documents/from-content",
                      json={"doc_type": "ic_memo", "content": GOOD, "dry_run": True}).json()
    assert dry["status"] == "validated" and store.published is None
    assert client.post("/api/v1/deals/missing/documents/from-content",
                       json={"doc_type": "ic_memo", "content": GOOD}).status_code == 404
    assert client.post("/api/v1/deals/deal-g/documents/from-content",
                       json={"doc_type": "ic_memo", "content": GOOD, "formats": ["exe"]}).status_code == 422
    assert client.post("/api/v1/deals/deal-g/documents/from-content",
                       json={"doc_type": "whitepaper", "content": GOOD}).status_code == 422
