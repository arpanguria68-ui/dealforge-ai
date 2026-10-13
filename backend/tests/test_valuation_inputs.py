import asyncio

import pytest

from app.agents.financial_analyst import ValuationAgent
from app.core.valuation_inputs import (
    build_wacc,
    derive_valuation_inputs,
    gather_market_inputs,
    project_cash_flows,
)

PRIOR = {
    "financial_analyst": {
        "revenue_analysis": {"annual_revenue": 457_547_000, "growth_rate": 12.72},
        "cash_flow": {"free_cash_flow": 39_321_000},
    },
    "market_researcher": {"competitive_landscape": {"key_players": []}},
}


async def fake_fetch(endpoint, symbol):
    if endpoint == "peers":
        return ["SPT", "AAA", "BBB", "CCC", "BAD"]
    metrics = {
        "SPT": {"beta": 1.0, "marketCapitalization": 667.0, "totalDebt/totalEquityQuarterly": 0.14,
                "evRevenueTTM": 1.2},
        "AAA": {"evRevenueTTM": 2.0}, "BBB": {"evRevenueTTM": 4.0}, "CCC": {"evRevenueTTM": 6.0},
        "BAD": {"evRevenueTTM": 500.0},  # data error, must be dropped
    }[symbol]
    return {"metric": metrics}


def test_derive_maps_only_explicit_values():
    got = derive_valuation_inputs(PRIOR)
    assert got["financial_data"]["revenue"] == 457_547_000
    assert got["financial_data"]["free_cash_flow"] == 39_321_000
    assert got["financial_data"]["revenue_growth"] == pytest.approx(0.1272)
    assert "wacc" not in got["financial_data"] and got["market_data"] == {}


def test_wacc_capm_math():
    w = build_wacc(1.0, 667.0, 93.0, {})
    ke = 0.0425 + 1.0 * 0.05 + 0.01
    assert w["cost_of_equity"] == pytest.approx(ke, abs=1e-4)
    assert 0.08 < w["wacc"] < ke


def test_projection_fades_growth_to_terminal():
    flows = project_cash_flows(100.0, 0.12, 0.025, 5)
    assert flows[0] == pytest.approx(112.0) and len(flows) == 5
    assert flows[-1] / flows[-2] - 1 == pytest.approx(0.025, abs=1e-3)


def test_gather_uses_median_and_drops_outliers():
    fin = derive_valuation_inputs(PRIOR)["financial_data"]
    got = asyncio.run(gather_market_inputs("SPT", fake_fetch, fin))
    assert got["market_data"]["ev_revenue_median"] == 4.0
    assert {p["ticker"] for p in got["market_data"]["peers"]} == {"AAA", "BBB", "CCC"}
    assert got["financial_data"]["wacc"] and len(got["financial_data"]["projected_cash_flows"]) == 5
    assert any("risk-free" in a for a in got["assumptions"]) and not got["missing"]


def test_gather_reports_missing_when_feed_is_empty():
    async def empty(endpoint, symbol):
        return None

    got = asyncio.run(gather_market_inputs("SPT", empty, {}))
    assert got["financial_data"] == {} and got["market_data"] == {}
    assert len(got["missing"]) >= 3


def test_valuation_agent_end_to_end_with_sourced_inputs(monkeypatch):
    agent = object.__new__(ValuationAgent)
    agent.name = "valuation_agent"

    async def fetch(endpoint, symbol):
        return await fake_fetch(endpoint, symbol)

    agent._finnhub_fetch = fetch

    async def run_method(method, financial_data, market_data):
        if method == "comparable_companies":
            return {"method": method,
                    "value": financial_data["revenue"] * market_data["ev_revenue_median"]}
        if method == "dcf":
            assert financial_data["wacc"] and financial_data["projected_cash_flows"]
            return {"method": method, "value": sum(financial_data["projected_cash_flows"])}
        return {"method": method, "value": None}

    agent._run_valuation_method = run_method
    result = asyncio.run(agent.run("value it", {"ticker": "SPT", "agent_outputs": PRIOR}))
    assert result.success
    assert result.data["audit"]["assumptions"] and result.data["audit"]["sources"]
    assert result.data["valuations"]["comparable_companies"]["value"] == pytest.approx(457_547_000 * 4.0)
    values = [v["value"] for v in result.data["valuations"].values() if v.get("value")]
    assert min(values) <= result.data["weighted_estimate"] <= max(values)


def test_valuation_agent_failure_lists_what_is_missing():
    agent = object.__new__(ValuationAgent)
    agent.name = "valuation_agent"
    result = asyncio.run(agent.run("value it", {"ticker": "not a ticker", "agent_outputs": {}}))
    assert not result.success
    assert "ticker" in result.reasoning


def test_curator_gathering_market_metrics_runs_before_modeling():
    from types import SimpleNamespace as NS
    from app.agents.project_manager import wire_stage_dependencies

    items = [
        NS(id="d", assigned_agent="data_curator", depends_on=[],
           title="Gather Financial Metrics and Comparable Company Data",
           description="Pull the latest share price, market cap and peer multiples."),
        NS(id="v", assigned_agent="valuation_agent", depends_on=[], title="DCF", description="Build DCF"),
        NS(id="m", assigned_agent="investment_memo_agent", depends_on=[], title="Memo", description="IC memo"),
    ]
    wire_stage_dependencies(items)
    assert items[0].depends_on == []          # data collection first
    assert "d" in items[1].depends_on         # modeling waits for data
    assert set(items[2].depends_on) == {"d", "v"}


def test_curator_retrieval_wording_still_counts_as_data_collection():
    from types import SimpleNamespace as NS
    from app.agents.project_manager import wire_stage_dependencies

    for title in ("Historical Financials and Market Data Retrieval", "Extraction of Filings", "Pull share price and peers"):
        items = [
            NS(id="d", assigned_agent="data_curator", depends_on=[], title=title,
               description="Historical financials, share price and market data for the target."),
            NS(id="v", assigned_agent="valuation_agent", depends_on=[], title="DCF", description="Build DCF"),
        ]
        wire_stage_dependencies(items)
        assert items[0].depends_on == [] and "d" in items[1].depends_on, title


def test_figures_reported_in_millions_are_converted_to_dollars():
    prior = {"data_curator": {"curated_deal_metrics": {"historical_financials": {
        "unit": "millions USD",
        "years": [{"fiscal_year": 2023, "revenue": 331, "free_cash_flow": 15},
                  {"fiscal_year": 2024, "revenue": 405, "free_cash_flow": 26},
                  {"fiscal_year": 2022, "revenue": 254, "free_cash_flow": 8}]}}}}
    fin = derive_valuation_inputs(prior)["financial_data"]
    assert fin["revenue"] == 405_000_000 and fin["free_cash_flow"] == 26_000_000   # latest year, in dollars


def test_dollar_figures_are_left_alone_and_unit_hints_do_not_leak():
    prior = {"financial_analyst": {"revenue_analysis": {"annual_revenue": 457_547_000}},
             "other": {"unit": "millions", "notes": {"revenue": 5}}}
    assert derive_valuation_inputs(prior)["financial_data"]["revenue"] == 457_547_000


def test_curate_peers_drops_size_outliers():
    from app.core.valuation_inputs import curate_peers

    rows = [{"ticker": t, "ev_revenue": 3.0, "market_cap": c}
            for t, c in [("A", 500), ("B", 900), ("C", 2000), ("BIG", 90000), ("TINY", 5)]]
    kept, dropped = curate_peers(rows, 600)
    assert [r["ticker"] for r in kept] == ["A", "B", "C"]
    assert set(dropped) == {"BIG", "TINY"}


def test_curate_peers_keeps_all_when_filter_too_aggressive():
    from app.core.valuation_inputs import curate_peers

    rows = [{"ticker": "A", "ev_revenue": 3.0, "market_cap": 500}, {"ticker": "BIG", "ev_revenue": 9.0, "market_cap": 90000}]
    assert curate_peers(rows, 600) == (rows, [])
    assert curate_peers(rows, None) == (rows, [])


def test_shared_assumptions_note_from_valuation_audit():
    from app.core.valuation_inputs import shared_assumptions_note

    results = [{"agent_type": "valuation_agent", "data": {"audit": {"inputs": {
        "financial_data": {"wacc": 0.1234, "revenue_growth": 0.12, "free_cash_flow": 50_000_000},
        "market_data": {"enterprise_value": 580_000_000}}}}}]
    note = shared_assumptions_note(results)
    assert "WACC 12.34%" in note and "$50,000,000" in note and "above it" in note
    assert shared_assumptions_note([{"agent_type": "x", "data": {}}]) == ""
