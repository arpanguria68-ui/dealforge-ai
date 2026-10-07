"""Hallucination-guard tests: local-client hygiene, Laya guardrail
enforcement, LM Studio registry, QualityGate.verify_stage.

All offline. Laya-dependent paths are fail-soft (LAYA_MODE=off) so these
pin the deterministic layers: sanitization, thresholds, parsing, registry.
"""
import inspect
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("LAYA_MODE", "off")


def test_lmstudio_generate_accepts_json_mode():
    from app.core.llm.local_llm_client import LMStudioClient

    sig = inspect.signature(LMStudioClient.generate)
    assert "json_mode" in sig.parameters
    assert sig.parameters["temperature"].default == pytest.approx(0.2)


def test_financial_fallback_preserves_inputs_without_inventing_growth():
    from app.agents.financial_analyst import FinancialAnalystAgent

    agent = FinancialAnalystAgent.__new__(FinancialAnalystAgent)
    result = agent._provided_metrics_assessment(
        "Acme has $10m ARR and $2m EBITDA; no debt disclosed."
    )

    assert result["revenue_analysis"]["arr"] == 10_000_000
    assert result["profitability"]["ebitda"] == 2_000_000
    assert result["revenue_analysis"]["growth_rate"] is None
    assert "attractive" not in result["investment_thesis"].lower()
    assert "growth trend" in result["investment_thesis"].lower()


def test_cedarbrook_shared_usd_millions_case_derives_only_supported_metrics():
    from app.agents.financial_analyst import FinancialAnalystAgent

    task = (
        "Fictional Cedarbrook Pumps. All amounts USD millions. FY23 revenue 20, EBITDA 2.8; "
        "FY24 revenue 24, EBITDA 3.6; FY25 revenue 30, gross profit 10.8, EBITDA 4.5. "
        "Debt 20, cash 5, enterprise value 90. Use only supplied information; "
        "LM Studio only. No cloud LLM, web search, external APIs, MCP, or fallback; "
        "if local inference fails, stop and report failure."
    )
    agent = FinancialAnalystAgent.__new__(FinancialAnalystAgent)
    result = agent._provided_metrics_assessment(task)

    assert FinancialAnalystAgent.is_input_only_request(task)
    assert [row["period"] for row in result["historical_financials"]] == ["FY2023", "FY2024", "FY2025"]
    assert result["revenue_analysis"]["growth_rate"] == pytest.approx(22.47)
    assert result["profitability"]["gross_margin"] == pytest.approx(36)
    assert result["profitability"]["ebitda_margin"] == pytest.approx(15)
    assert result["balance_sheet"]["net_debt"] == 15_000_000
    assert result["valuation"]["equity_value"] == 75_000_000
    assert result["valuation"]["ev_ebitda_multiple"] == 20
    assert result["valuation"]["multiple_estimate"] is None


def test_financial_assessment_maps_repeated_fiscal_year_shorthand_with_caveat():
    from app.agents.financial_analyst import FinancialAnalystAgent

    task = (
        "LOCAL-ONLY UAT: Fictional Cedarbrook Pumps. Synthetic USDm: FY23 revenue 20 and EBITDA 2.8; "
        "FY24 24 and 3.6; FY25 30, gross profit 10.8, EBITDA 4.5; debt 20, cash 5, EV 90. "
        "Calculate revenue CAGR, gross/EBITDA margins, net debt, EV/EBITDA, and equity value."
    )
    result = FinancialAnalystAgent.__new__(FinancialAnalystAgent)._provided_metrics_assessment(task)

    assert [row["revenue"] for row in result["historical_financials"]] == [20_000_000, 24_000_000, 30_000_000]
    assert [row["ebitda"] for row in result["historical_financials"]] == [2_800_000, 3_600_000, 4_500_000]
    assert result["revenue_analysis"]["growth_rate"] == pytest.approx(22.47)
    assert result["profitability"]["gross_profit"] == 10_800_000
    assert result["profitability"]["gross_margin"] == pytest.approx(36)
    assert result["profitability"]["ebitda_margin"] == pytest.approx(15)
    assert result["balance_sheet"]["net_debt"] == 15_000_000
    assert result["valuation"]["ev_ebitda_multiple"] == 20
    assert result["valuation"]["equity_value"] == 75_000_000
    assert any("unlabeled fiscal-year shorthand" in item for item in result["data_limitations"])
    assert result["historical_financials"][1]["source_type"] == "user_supplied_shorthand_inferred"


@pytest.mark.asyncio
async def test_laya_financial_gate_is_local_only_and_flags_for_review(monkeypatch):
    from app.agents.financial_analyst import FinancialAnalystAgent
    from app.core.laya import client as laya_client

    agent = FinancialAnalystAgent.__new__(FinancialAnalystAgent)
    analysis = {"reasoning": "EV/EBITDA is 20x.", "valuation": {"ev_ebitda_multiple": 20}}

    class RemoteClient:
        backend = "remote"

        async def gate_confidence(self, _payload):
            raise AssertionError("Financial figures must not be sent to remote Laya")

    monkeypatch.setattr(laya_client, "get_laya_client", lambda: RemoteClient())
    await agent._apply_laya_output_gate("confidential deal inputs", analysis)
    assert "laya_quality_gate" not in analysis

    class LocalClient:
        backend = "lmstudio"

        async def gate_confidence(self, payload):
            assert "confidential deal inputs" in payload
            return {"supported_p": 0.2, "red_flag_p": 0.8, "backend": "lmstudio"}

    monkeypatch.setattr(laya_client, "get_laya_client", lambda: LocalClient())
    await agent._apply_laya_output_gate("confidential deal inputs", analysis)
    assert analysis["laya_quality_gate"]["decision"] == "human_review"
    assert analysis["human_review_required"] is True


@pytest.mark.asyncio
async def test_financial_calculator_fails_closed_for_missing_or_zero_denominator():
    from app.core.tools.tool_router import FinancialCalculatorTool

    tool = FinancialCalculatorTool()
    missing = await tool.execute(calculation_type="ratio", inputs={"numerator": 90})
    zero = await tool.execute(
        calculation_type="ratio", inputs={"numerator": 90, "denominator": 0}
    )
    valid = await tool.execute(
        calculation_type="ratio",
        inputs={"numerator": 90, "denominator": 4.5, "ratio_name": "ev_ebitda"},
    )

    assert not missing.success
    assert not zero.success
    assert valid.success
    assert valid.data["ev_ebitda"] == 20


def test_saas_case_metrics_keep_labels_and_only_derive_supported_runway():
    from app.agents.financial_analyst import FinancialAnalystAgent

    agent = FinancialAnalystAgent.__new__(FinancialAnalystAgent)
    result = agent._provided_metrics_assessment(
        "Fictional SaaS: ARR $12.0M, YoY ARR growth 8%, gross margin 76%, "
        "gross revenue retention 91%, net revenue retention 104%, CAC payback 22 months, "
        "LTV/CAC 2.4x, annualized net burn $1.8M, cash $6.0M. "
        "Use only supplied metrics; do not invent historical data."
    )

    assert result["revenue_analysis"]["arr"] == 12_000_000
    assert result["revenue_analysis"]["growth_rate"] == 8
    assert result["profitability"]["gross_margin"] == 76
    assert result["profitability"]["ebitda_margin"] is None
    assert result["customer_metrics"]["gross_revenue_retention"] == 91
    assert result["customer_metrics"]["net_revenue_retention"] == 104
    assert result["customer_metrics"]["cac_payback_months"] == 22
    assert result["customer_metrics"]["ltv_to_cac"] == 2.4
    assert result["cash_flow"]["annualized_net_burn"] == 1_800_000
    assert result["cash_flow"]["cash_runway_months"] == 40
    assert "EBITDA margin: 76%" not in result["reasoning"]
    assert "Gross margin: 76% (user supplied)" in result["reasoning"]
    assert "Cash runway: 40 months" in result["reasoning"]
    assert "Gross revenue retention: 91%" in result["investment_thesis"]


def test_explicit_api_retrieval_request_does_not_trigger_input_only_shortcut():
    from app.agents.financial_analyst import FinancialAnalystAgent

    assert not FinancialAnalystAgent.is_input_only_request(
        "Use only supplied facts, do not invent external facts. If available, retrieve "
        "one peer datapoint using the financial API."
    )


@pytest.mark.asyncio
async def test_financial_analyst_honors_no_external_data_without_llm(monkeypatch):
    from app.agents.financial_analyst import FinancialAnalystAgent

    agent = FinancialAnalystAgent()

    async def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("No-external-data assessment should stay deterministic")

    monkeypatch.setattr(agent, "generate_with_tools", unexpected_llm_call)
    result = await agent.run(
        "Quick focused valuation sanity check for fictional Acme Widgets: "
        "$10m ARR, $2m EBITDA, no debt disclosed. Do not fetch external data; "
        "state assumptions and missing inputs."
    )

    assert result.success
    assert result.data["revenue_analysis"]["arr"] == 10_000_000
    assert result.data["revenue_analysis"]["growth_rate"] is None
    assert result.data["profitability"]["ebitda"] == 2_000_000
    assert result.data["profitability"]["ebitda_margin"] is None
    assert result.confidence == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_financial_analyst_uses_only_supplied_figures_and_keeps_unknowns_null(monkeypatch):
    from app.agents.financial_analyst import FinancialAnalystAgent

    agent = FinancialAnalystAgent()

    async def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("Input-only analysis must not ask the LLM to fill missing facts")

    monkeypatch.setattr(agent, "generate_with_tools", unexpected_llm_call)
    result = await agent.run(
        "Assess fictional Northstar Components. Supplied FY2025 figures: revenue $24.0M, "
        "gross profit $9.6M, EBITDA $3.0M, cash $2.0M, debt $6.0M, and 3-year "
        "revenue CAGR 12%. Use only these supplied figures; do not add external facts."
    )

    assert result.success
    assert result.confidence == pytest.approx(0.35)
    assert result.data["revenue_analysis"]["annual_revenue"] == 24_000_000
    assert result.data["revenue_analysis"]["growth_rate"] == pytest.approx(12)
    assert result.data["profitability"]["gross_margin"] == pytest.approx(40)
    assert result.data["profitability"]["ebitda_margin"] == pytest.approx(12.5)
    assert result.data["balance_sheet"]["net_debt"] == 4_000_000
    assert result.data["balance_sheet"]["net_debt_to_ebitda"] == pytest.approx(1.33)
    assert result.data["cash_flow"]["operating_cash_flow"] is None
    assert result.data["cash_flow"]["free_cash_flow"] is None
    assert result.data["cash_flow"]["burn_rate"] is None
    assert result.data["valuation"]["dcf_estimate"] is None
    assert result.data["valuation"]["multiple_estimate"] is None


@pytest.mark.asyncio
async def test_cedarbrook_run_path_stays_offline_and_returns_grounded_analysis(monkeypatch):
    from app.agents.financial_analyst import FinancialAnalystAgent

    agent = FinancialAnalystAgent()

    async def unexpected_llm(*args, **kwargs):
        raise AssertionError("Explicit offline analysis must not call an LLM")

    async def skip_gate(*args, **kwargs):
        return None

    monkeypatch.setattr(agent, "generate_with_tools", unexpected_llm)
    monkeypatch.setattr(agent, "_apply_laya_output_gate", skip_gate)
    result = await agent.run(
        "Fictional Cedarbrook Pumps. All amounts USD millions. FY23 revenue 20.0, EBITDA 2.8; "
        "FY24 revenue 24.0, EBITDA 3.6; FY25 revenue 30.0, gross profit 10.8, EBITDA 4.5; "
        "debt 20.0, cash 5.0, enterprise value 90.0. Calculate FY23-FY25 revenue CAGR, "
        "gross margin, EBITDA margin, net debt, EV/EBITDA, and implied equity value. "
        "Use only supplied inputs; no cloud LLM, web search, external APIs, or MCP."
    )

    assert result.success
    assert result.data["revenue_analysis"]["growth_rate"] == pytest.approx(22.47)
    assert result.data["profitability"]["gross_margin"] == pytest.approx(36)
    assert result.data["profitability"]["ebitda_margin"] == pytest.approx(15)
    assert result.data["balance_sheet"]["net_debt"] == 15_000_000
    assert result.data["valuation"]["ev_ebitda_multiple"] == 20
    assert result.data["valuation"]["equity_value"] == 75_000_000


def test_financial_grounding_discards_unsupported_local_model_claims():
    from app.agents.financial_analyst import FinancialAnalystAgent

    analysis = {
        "cash_flow": {
            "operating_cash_flow": 2_000_000,
            "free_cash_flow": -3_000_000,
            "burn_rate": 1_000_000,
        },
        "valuation": {
            "dcf_estimate": 5_000_000,
            "multiple_estimate": 20_000_000,
            "confidence_range": {"low": 4_000_000, "high": 6_000_000},
        },
        "financial_risks": ["High debt-to-equity ratio; leverage is risky."],
        "recommendation": "proceed",
    }
    task = (
        "Assess a fictional company. Do not invent cash flow or valuation."
    )
    context = {"financial_data": {
        "revenue": 20_000_000, "gross_profit": 7_000_000,
        "ebitda": 2_000_000, "cash": 2_000_000, "total_debt": 5_000_000,
    }}

    FinancialAnalystAgent.__new__(FinancialAnalystAgent)._enforce_evidence_grounding(
        analysis, task, context, []
    )

    assert all(value is None for value in analysis["cash_flow"].values())
    assert analysis["valuation"]["dcf_estimate"] is None
    assert analysis["valuation"]["multiple_estimate"] is None
    assert analysis["valuation"]["confidence_range"] == {"low": None, "high": None}
    assert all("debt-to-equity" not in risk.lower() for risk in analysis["financial_risks"][:-1])
    assert "debt-to-equity cannot be assessed" in analysis["financial_risks"][-1]
    assert analysis["recommendation"] == "caution"
    assert analysis["recommendation_limitations"]
    assert analysis["confidence_limitations"]
    assert FinancialAnalystAgent.__new__(FinancialAnalystAgent)._calculate_confidence(analysis) <= 0.5


def test_no_invention_instruction_still_allows_external_source_retrieval():
    from app.agents.financial_analyst import FinancialAnalystAgent

    assert not FinancialAnalystAgent.is_input_only_request(
        "Do not invent cash flow or valuation."
    )


def test_financial_grounding_keeps_cash_flow_from_successful_statement_tool():
    from app.agents.financial_analyst import FinancialAnalystAgent

    analysis = {"cash_flow": {
        "operating_cash_flow": 2_000_000,
        "free_cash_flow": -3_000_000,
        "burn_rate": 1_000_000,
    }}
    tools = [{
        "name": "fetch_financial_statements", "success": True,
        "data": {"operating_cash_flow": 2_000_000},
    }]

    FinancialAnalystAgent.__new__(FinancialAnalystAgent)._enforce_evidence_grounding(
        analysis, "Analyze this company", {}, tools
    )

    assert analysis["cash_flow"]["operating_cash_flow"] == 2_000_000
    assert analysis["cash_flow"]["free_cash_flow"] is None
    assert analysis["cash_flow"]["burn_rate"] is None


def test_financial_grounding_normalizes_cagr_and_recalculates_margins():
    from app.agents.financial_analyst import FinancialAnalystAgent

    analysis = {
        "revenue_analysis": {"annual_revenue": 99, "growth_rate": 99},
        "profitability": {
            "gross_profit": 7_000_000, "gross_margin": 99,
            "ebitda": 2_000_000, "ebitda_margin": 99,
        },
    }
    context = {"financial_data": {
        "revenue": 20_000_000, "gross_profit": 7_000_000,
        "ebitda": 2_000_000, "revenue_cagr": 0.08,
    }}

    FinancialAnalystAgent.__new__(FinancialAnalystAgent)._enforce_evidence_grounding(
        analysis, "Analyze provided data", context, []
    )

    assert analysis["revenue_analysis"]["annual_revenue"] == 20_000_000
    assert analysis["revenue_analysis"]["growth_rate"] == 8
    assert analysis["profitability"]["gross_margin"] == 35
    assert analysis["profitability"]["ebitda_margin"] == 10


def test_financial_grounding_reconciles_valuation_and_leverage_conflicts():
    from app.agents.financial_analyst import FinancialAnalystAgent

    task = (
        "Fictional Cedarbrook Pumps. All amounts USD millions. FY23 revenue 20, EBITDA 2.8; "
        "FY24 revenue 24, EBITDA 3.6; FY25 revenue 30, gross profit 10.8, EBITDA 4.5. "
        "Debt 20, cash 5, enterprise value 90. Calculate revenue CAGR and valuation."
    )
    analysis = {
        "revenue_analysis": {"growth_rate": 25},
        "profitability": {"gross_margin": 36, "ebitda_margin": 15},
        "balance_sheet": {"net_debt": 15, "net_debt_to_ebitda": 6.7},
        "valuation": {"multiple_estimate": 3, "equity_value": 75},
        "reasoning": "The model-proposed multiple is 3.0x despite the arithmetic below.",
    }

    FinancialAnalystAgent.__new__(FinancialAnalystAgent)._enforce_evidence_grounding(
        analysis, task, {}, []
    )

    assert analysis["revenue_analysis"]["growth_rate"] == pytest.approx(22.47)
    assert analysis["balance_sheet"]["net_debt"] == pytest.approx(15_000_000)
    assert analysis["balance_sheet"]["net_debt_to_ebitda"] == pytest.approx(3.33)
    assert analysis["valuation"]["ev_ebitda_multiple"] == pytest.approx(20)
    assert analysis["valuation"]["multiple_estimate"] is None
    assert analysis["valuation"]["equity_value"] == pytest.approx(75_000_000)
    assert analysis["human_review_required"] is True
    assert {item["metric"] for item in analysis["metric_conflicts"]} >= {
        "revenue_growth_rate", "net_debt_to_ebitda", "ev_ebitda_multiple",
    }


def test_unmapped_agents_receive_no_tool_schemas():
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter.__new__(ToolRouter)
    router.tools = {"sensitive_tool": object()}

    assert router.list_tools(agent_name="unmapped_agent") == []


def test_laya_tool_families_use_registered_data_tool_names():
    from app.core.tools.tool_router import ToolRouter

    assert "finnhub_data" in ToolRouter.LAYA_FAMILY_TOOLS["financial"]
    assert "legal_clause_analyzer" in ToolRouter.LAYA_FAMILY_TOOLS["legal_risk"]
    assert "finnhub" not in ToolRouter.LAYA_FAMILY_TOOLS["financial"]
    assert "legal_clause" not in ToolRouter.LAYA_FAMILY_TOOLS["legal_risk"]


@pytest.mark.asyncio
async def test_lmstudio_json_schema_reads_json_from_reasoning_field():
    from app.core.llm.local_llm_client import LMStudioClient

    captured = {}

    async def create(**params):
        captured.update(params)
        message = SimpleNamespace(
            content="",
            reasoning_content='{"arr_usd":10000000,"growth_rate":null}',
            tool_calls=None,
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = LMStudioClient()
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await client.generate("Return JSON", json_mode=True)

    assert captured["response_format"]["type"] == "json_schema"
    assert result["content"] == '{"arr_usd":10000000,"growth_rate":null}'


def test_ollama_defaults_deterministic():
    from app.core.llm.local_llm_client import OllamaClient, LOCAL_DEFAULT_TEMPERATURE

    assert LOCAL_DEFAULT_TEMPERATURE == pytest.approx(0.2)
    sig = inspect.signature(OllamaClient.generate)
    assert sig.parameters["temperature"].default == pytest.approx(0.2)


def test_extract_json_block_strips_fences():
    from app.core.llm.local_llm_client import _extract_json_block

    fenced = 'Here you go:\n```json\n{"a": 1}\n```'
    assert _extract_json_block(fenced) == '{"a": 1}'
    assert _extract_json_block('  {"a": 1}  ') == '{"a": 1}'
    assert _extract_json_block('') == ''


def test_safe_json_loads():
    from app.core.llm.local_llm_client import _safe_json_loads

    assert _safe_json_loads({"a": 1}) == {"a": 1}
    assert _safe_json_loads('{"a": 1}') == {"a": 1}
    assert _safe_json_loads('```json\n{"a": 1}\n```') == {"a": 1}
    bad = _safe_json_loads('{not json')
    assert bad == {"_raw": '{not json'}


def test_clamp_temperature():
    from app.core.llm.local_llm_client import _clamp_temperature

    assert _clamp_temperature(0.7) == pytest.approx(0.7)
    assert _clamp_temperature(5.0) == pytest.approx(1.0)
    assert _clamp_temperature(-1.0) == pytest.approx(0.0)
    assert _clamp_temperature('junk') == pytest.approx(0.2)


def test_sanitize_brief_redacts_secrets():
    from app.core.laya.graph_nodes import sanitize_brief

    text = (
        "Analyze Acme. api_key=sk-1234567890abcdef pumped. "
        "Login with password=hunter2please. "
        "Header: Bearer abcdefghijklmnop. "
        "Also AIzaSyD-1234567890abcdefghij here."
    )
    out = sanitize_brief(text)
    assert "sk-1234567890abcdef" not in out
    assert "hunter2please" not in out
    assert "abcdefghijklmnop" not in out
    assert "AIzaSyD-1234567890abcdefghij" not in out
    assert "Acme" in out  # benign content preserved
    assert sanitize_brief("") == ""


def test_sanitize_brief_truncates():
    from app.core.laya.graph_nodes import sanitize_brief

    out = sanitize_brief("x" * 7000, max_chars=100)
    assert len(out) < 7000
    assert "truncated" in out


def test_laya_guard_action_mapping():
    from app.core.laya.graph_nodes import laya_guard_action

    assert laya_guard_action(None) == "allow"
    assert laya_guard_action({}) == "allow"
    assert laya_guard_action({"injection_p": 0.1, "sensitive_p": 0.1}) == "allow"
    assert laya_guard_action({"injection_p": 0.9, "sensitive_p": 0.0}) == "quarantine"
    assert laya_guard_action({"injection_p": 0.6, "sensitive_p": 0.0}) == "review"
    assert laya_guard_action({"injection_p": 0.1, "sensitive_p": 0.7}) == "review"


def test_lmstudio_registry_provider_aware():
    from app.core.llm.model_registry import get_capabilities

    lm = get_capabilities("local-model", "lmstudio")
    assert lm.json_mode is True
    assert lm.tool_calling is False

    # Same id, different provider → different capabilities
    assert get_capabilities("llama3.1", "lmstudio").tool_calling is False
    assert get_capabilities("llama3.1", "ollama").tool_calling is True

    # Unknown LM Studio model → conservative JSON-capable default
    unk = get_capabilities("some-future-8b", "lmstudio")
    assert unk.json_mode is True


@pytest.mark.asyncio
async def test_quality_gate_verify_stage_blocks_critical():
    from app.core.quality.gates import QualityGate
    from app.orchestrator.state import DealStage

    bad = await QualityGate.verify_stage(
        stage=DealStage.SCORING,
        data={"risks": [{"severity": 9}, {"severity": 10}], "confidence": 0.9},
        threshold=0.8,
    )
    assert bad["blocked"] is True
    assert bad["reasons"]

    good = await QualityGate.verify_stage(
        stage=DealStage.SCORING,
        data={"risks": [], "confidence": 0.9},
        threshold=0.8,
    )
    assert good["blocked"] is False
    assert good["passed"] is True
