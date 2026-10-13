import asyncio
import json

from app.agents.financial_analyst import ValuationAgent
from app.core.tools.finance_toolkit_tool import _jsonable
from app.core.valuation_inputs import derive_valuation_inputs

CURATOR = {"curated_deal_metrics": {"historical_financials_millions_usd": {
    "2024": {"revenue": 405.91, "free_cash_flow": None},
    "2025": {"revenue": 457.55, "free_cash_flow": None}}}}


def test_year_keyed_records_with_the_unit_in_the_key_name_are_read_in_dollars():
    fin = derive_valuation_inputs({"data_curator": CURATOR})["financial_data"]
    assert fin["revenue"] == 457_550_000 and "free_cash_flow" not in fin


def test_dataframe_style_dicts_become_json_safe():
    nan = float("nan")
    out = _jsonable({("Intrinsic Value", "AAPL"): {"2025-12-31": 1.5, ("a", "b"): nan}})
    assert json.loads(json.dumps(out)) == {"Intrinsic Value | AAPL": {"2025-12-31": 1.5, "a | b": None}}


FILINGS = {
    "has_data": True, "entity_name": "Acme, Inc.",
    "income_statement": {"revenue": {"2024": 400.0e6, "2025": 457.5e6}, "gross_profit": {"2024": 300e6, "2025": 354e6},
                         "_sources": {"revenue": {"2025": {"url": "https://www.sec.gov/b", "form": "10-K", "end": "2025-12-31"}}}},
    "balance_sheet": {},
    "cash_flow": {"cfo": {"2024": 30e6, "2025": 43.4e6}, "capex": {"2024": 3e6, "2025": 4.1e6},
                  "_sources": {"cfo": {"2025": {"url": "https://www.sec.gov/b", "form": "10-K"}}}},
}


class _Tools:
    async def execute(self, name, params):
        assert name == "fetch_financial_statements" and params["ticker"] == "SPT"
        return type("R", (), {"success": True, "data": FILINGS, "error": None})()


def test_missing_free_cash_flow_and_growth_are_read_from_the_filings():
    agent = object.__new__(ValuationAgent)
    agent.tools = _Tools()
    financial, audit = {"revenue": 457.5e6}, {"sources": [], "assumptions": [], "missing": []}
    asyncio.run(agent._fill_from_filings(financial, "SPT", audit))
    assert round(financial["free_cash_flow"]) == 39_300_000          # CFO 43.4M - capex 4.1M
    assert abs(financial["revenue_growth"] - 0.14375) < 1e-4          # 457.5 / 400 - 1
    assert any("SEC EDGAR" in s and "free cash flow" in s for s in audit["sources"])


def test_a_failed_filing_fetch_changes_nothing():
    class Down:
        async def execute(self, *_a, **_k):
            raise RuntimeError("network")

    agent = object.__new__(ValuationAgent)
    agent.tools = Down()
    financial, audit = {}, {"sources": [], "assumptions": [], "missing": []}
    asyncio.run(agent._fill_from_filings(financial, "SPT", audit))
    assert financial == {} and audit["sources"] == []
