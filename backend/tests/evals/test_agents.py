"""Offline behavioral checks for agent contracts (not subjective LLM benchmarks)."""
import pytest

from app.agents.financial_analyst import FinancialAnalystAgent
from app.agents.legal_advisor import LegalAdvisorAgent


@pytest.mark.asyncio
async def test_financial_analyst_input_only_calculates_supported_metrics(mock_financial_analyst, monkeypatch):
    async def no_op_gate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mock_financial_analyst, "_apply_laya_output_gate", no_op_gate)
    task = (
        "Using only supplied figures; do not fetch external data. FY2023 revenue $20m, "
        "FY2025 revenue $30m, EBITDA $4.5m, gross profit $10.8m, debt $20m, "
        "cash $5m and enterprise value $90m. Calculate growth and valuation metrics."
    )
    result = await mock_financial_analyst.run(task=task, context={})
    assert result.success
    assert result.data["derived_growth"]["revenue_cagr_percent"] == pytest.approx(22.47, abs=0.02)
    assert result.data["balance_sheet"]["net_debt"] == pytest.approx(15_000_000)
    assert result.data["valuation"]["ev_ebitda_multiple"] == pytest.approx(20.0)
    assert result.data["cash_flow"]["free_cash_flow"] is None


@pytest.mark.asyncio
async def test_financial_analyst_does_not_invent_missing_metrics(mock_financial_analyst):
    task = "Use only supplied figures; do not fetch external data."
    result = await mock_financial_analyst.run(task=task, context={})
    assert not result.success
    assert result.data["error"] == "input_metrics_not_found"


def test_legal_prompt_preserves_supplied_clause_and_limits():
    agent = LegalAdvisorAgent(llm_client=object())
    prompt = agent._build_analysis_prompt(
        "Identify change-of-control exposure",
        {"clauses": [{"text": "Consent required upon change of control."}]},
        [],
    )
    assert "Consent required upon change of control." in prompt
    assert "Identify change-of-control exposure" in prompt
    assert "No legal documents retrieved" in prompt


def test_financial_input_only_intent_detection():
    assert FinancialAnalystAgent.is_input_only_request("Use only supplied data; no external data")
    assert not FinancialAnalystAgent.is_input_only_request("Fetch recent public financial statements")
