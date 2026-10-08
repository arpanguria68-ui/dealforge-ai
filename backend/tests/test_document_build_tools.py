"""Every document/deck path produces real files built by Python libraries.

Each artifact is re-opened with python-docx / python-pptx / openpyxl / pypdf
(ReportGuardrails.deep_validate). No path may return LLM text as a document.
"""

import base64
import io
from types import SimpleNamespace

import pytest

from app.core.reports.report_guardrails import ReportGuardrails

DEAL = {"id": "deal-files", "name": "Project Acme", "target_company": "Acme SaaS", "industry": "software",
        "status": "completed", "current_stage": "deep_dive"}
RESULTS = [
    {"agent_type": "financial_analyst", "success": True, "data": {
        "summary": "Revenue grew.", "key_findings": ["Revenue up 18%", "Margins expanded"],
        "sources": [{"url": "https://sec.gov/acme", "title": "Acme 10-K"}],
        "historical_financials": [{"fiscal_year": "FY2024", "revenue": 118.0, "source_url": "https://sec.gov/acme"}],
        "valuation": {"enterprise_value": 950.0, "wacc": 0.095}}},
    {"agent_type": "risk_assessor", "success": True, "data": {"risks": [
        {"risk": "Customer concentration", "severity": 4, "category": "Commercial", "mitigation": "Diversify"}]}},
]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from app.core.knowledge_graph import service
    from app.core.tools import reporting_tools

    async def no_risks(deal_id, limit=10):
        return []

    monkeypatch.setattr(service, "risk_register", no_risks)
    monkeypatch.setattr(reporting_tools, "OUTPUT_DIR", tmp_path)


def _assert_real(fmt, content):
    check = ReportGuardrails.deep_validate(fmt, content)
    assert check["valid"], (fmt, check)
    return check


@pytest.fixture(scope="module")
def router():
    from app.core.tools.tool_router import ToolRouter

    r = ToolRouter()
    r.register_default_tools(object())
    return r


def test_legacy_generators_build_valid_files():
    from app.core.reports.document_planner import _deterministic_synthesis
    from app.core.reports.report_generator import generate_docx, generate_excel, generate_pdf, generate_pptx

    analyst = _deterministic_synthesis({"data_points": [], "findings": [], "unknowns": [], "sources": []})
    analyst["risk_matrix"] = [{"risk": "Customer concentration", "severity": 8, "category": None, "mitigation": None}]
    for fmt, fn in (("docx", generate_docx), ("pptx", generate_pptx), ("xlsx", generate_excel), ("pdf", generate_pdf)):
        _assert_real(fmt, fn(DEAL, analyst, RESULTS))


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["docx", "pdf", "pptx", "excel"])
async def test_generate_report_tool_returns_validated_files(router, fmt):
    res = await router.execute("generate_report", {
        "format": fmt, "deal_context": DEAL, "analyst_data": {}, "agent_results": RESULTS})
    assert res.success, res.error
    ext = res.data["file_extension"]
    _assert_real(ext, base64.b64decode(res.data["file_bytes_base64"]))
    assert res.data["validation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_text,formats", [
    ("IC memo", ["docx", "pdf"]), ("board deck", ["pptx"]), ("risk report in excel", ["xlsx"]),
])
async def test_build_document_tool_builds_each_format(router, request_text, formats):
    res = await router.execute("build_document", {"request": request_text, "deal": DEAL, "agent_results": RESULTS})
    assert res.success, res.error
    assert sorted(res.data["files_base64"]) == sorted(formats)
    for fmt, b64 in res.data["files_base64"].items():
        _assert_real(fmt, base64.b64decode(b64))


@pytest.mark.asyncio
async def test_build_document_refuses_without_evidence(router):
    res = await router.execute("build_document", {"request": "IC memo", "deal": DEAL})
    assert not res.success and "agent_results" in res.error


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["pdf", "docx"])
async def test_ic_memo_tool_writes_validated_files(router, fmt):
    res = await router.execute("generate_ic_memo", {
        "ticker": "ACME", "deal_name": "Project Acme", "format": fmt,
        "sections": {"executive_summary": "Acme is a SaaS vendor.", "risks": "Customer concentration."}})
    assert res.success, res.error
    assert res.data["memo_path"].endswith(f".{fmt}") and res.data["validation"]
    with open(res.data["memo_path"], "rb") as handle:
        _assert_real(fmt, handle.read())


@pytest.mark.asyncio
async def test_deal_deck_and_meeting_memo_tools_write_validated_files(router):
    deck = await router.execute("generate_deal_deck", {"ticker": "ACME", "deal_name": "Project Acme",
                                                       "agent_results": RESULTS})
    assert deck.success, deck.error
    with open(deck.data["deck_path"], "rb") as handle:
        _assert_real("pptx", handle.read())
    memo = await router.execute("generate_meeting_memo", {"deal_name": "Project Acme", "meeting_type": "IC Meeting", "agent_results": RESULTS})
    assert memo.success, memo.error
    with open(memo.data["memo_path"], "rb") as handle:
        _assert_real(memo.data["memo_path"].rsplit(".", 1)[1], handle.read())


def test_reporting_tools_never_write_to_hardcoded_windows_paths():
    from app.core import paths
    from app.core.tools import excel_model_engine

    assert "F:" not in str(paths.output_dir()) and "F:" not in str(excel_model_engine.OUTPUT_DIR)
    assert all("F:" not in d for d in paths.knowledge_dirs())


@pytest.mark.asyncio
async def test_compiler_builds_files_even_when_llm_skips_the_tool(router):
    """Files come from the generate_report tool, not from the model's choice to call it."""
    from app.agents.compiler_agent import ReportCompilerAgent

    agent = ReportCompilerAgent.__new__(ReportCompilerAgent)
    agent.name = "compiler_agent"
    agent.llm = object()
    agent.tools = router
    agent.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                   error=lambda *a, **k: None)

    async def narrative_only(prompt, system_prompt=None):
        return {"content": '{"reasoning": "narrative only", "key_takeaways": []}'}

    agent.generate_with_tools = narrative_only
    out = await agent.run("Compile", {"formats": ["pptx", "excel"], "deal_state": dict(DEAL),
                                      "agent_results": RESULTS})
    assert out.success
    assert sorted(out.data["generated_formats"]) == ["pptx", "xlsx"]
    assert set(out.data["built_by"].values()) == {"deterministic_tool_call"}
    for ext, b64 in out.data["files_base64"].items():
        _assert_real(ext, base64.b64decode(b64))


@pytest.mark.asyncio
async def test_investment_memo_agent_ships_python_built_files(router):
    from app.agents.investment_memo_agent import InvestmentMemoAgent

    agent = InvestmentMemoAgent.__new__(InvestmentMemoAgent)
    agent.name = "investment_memo_agent"
    agent.tools = router
    agent.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                   error=lambda *a, **k: None)

    async def draft(prompt, system_prompt=None):
        return {"content": "# Executive Summary\nDraft prose."}

    agent.generate_with_tools = draft
    out = await agent.run("Draft IC memo", {"deal_id": "deal-files", "company_name": "Acme SaaS",
                                            "agent_results": RESULTS})
    assert out.success and out.data["file_status"] == "built"
    assert sorted(out.data["files"]) == ["docx", "pdf"]
    for fmt, b64 in out.data["files"].items():
        _assert_real(fmt, base64.b64decode(b64))


def test_fake_documents_are_rejected():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<root/>")
    assert not ReportGuardrails.validate_artifact("docx", buf.getvalue())["valid"]
    assert not ReportGuardrails.validate_artifact("pdf", b"%PDF-1.4\n# Markdown memo\n%%EOF")["valid"]


def test_agents_with_file_tools_are_told_to_build_files():
    from app.agents.base import BaseAgent

    guidance = BaseAgent._deliverable_guidance(["web_search", "generate_report", "build_document"])
    assert "`build_document`" in guidance and "Never present" in guidance
    assert "`generate_ic_memo`" in BaseAgent._deliverable_guidance(["generate_ic_memo"])
    assert BaseAgent._deliverable_guidance(["web_search", "financial_calculator"]) == ""
