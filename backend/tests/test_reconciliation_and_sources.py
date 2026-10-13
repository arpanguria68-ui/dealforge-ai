from types import SimpleNamespace

from app.agents.financial_analyst import FinancialAnalystAgent
from app.core.validation.reconciliation import parse_money, reconcile, reconciliation_notice


def test_money_parsing_handles_strings_numbers_and_units():
    assert parse_money("$270.0M") == 270e6 and parse_money("$1.2B") == 1.2e9
    assert parse_money(785_400_000) == 785_400_000
    assert parse_money(270.0, "entry_ev_m") == 270e6
    assert parse_money(12.5) is None and parse_money("n/a") is None and parse_money(True) is None


LBO = {"lbo_model_analysis": {"sponsor_entry_valuation": {"entry_ev": "$270.0M"}}}


def test_lbo_entry_below_market_value_is_flagged():
    issues = reconcile([{"agent_type": "dcf_lbo_architect", "data": LBO}], market_ev=579.5e6)
    assert len(issues) == 1 and issues[0]["metric"] == "LBO entry vs market value"
    assert "premium" in issues[0]["message"] and "$270.0M" in issues[0]["message"]
    assert "do not average" in reconciliation_notice(issues)
    assert reconcile([{"agent_type": "dcf_lbo_architect", "data": LBO}], market_ev=250e6) == []
    assert reconcile([{"agent_type": "dcf_lbo_architect", "data": LBO}]) == []   # no market figure: nothing to compare


def test_conflicting_dcfs_are_flagged_only_beyond_the_tolerance():
    def results(a, b):
        return [
            {"agent_type": "valuation_agent", "data": {"valuations": {"dcf": {"value": a}}}},
            {"agent_type": "advanced_financial_modeler",
             "data": {"dcf_valuation_model": {"scenarios": {"base": {"enterprise_value": b}, "bull": {"enterprise_value": b * 2}}}}},
        ]
    assert reconcile(results(700e6, 740e6)) == []                       # 6% apart: fine
    issues = reconcile(results(700e6, 900e6))                           # 29% apart; the bull case is ignored
    assert len(issues) == 1 and issues[0]["metric"] == "DCF enterprise value"


FILINGS = {
    "has_data": True, "entity_name": "Acme, Inc.",
    "income_statement": {
        "revenue": {"2024": 400.0e6, "2025": 457.5e6},
        "gross_profit": {"2024": 310.0e6, "2025": 354.8e6},
        "_sources": {"revenue": {
            "2024": {"url": "https://www.sec.gov/a", "form": "10-K", "filed": "2025-02-01", "end": "2024-12-31", "reported_fy": "2024"},
            "2025": {"url": "https://www.sec.gov/b", "form": "10-K", "filed": "2026-02-01", "end": "2025-12-31", "reported_fy": "2025"}}},
    },
    "balance_sheet": {}, "cash_flow": {},
}


def test_model_analysis_keeps_its_prose_but_gains_the_filing_links():
    analysis = {"revenue_analysis": {"annual_revenue": 1, "quality_assessment": "High-quality recurring revenue."},
                "cash_flow": {"free_cash_flow": 39_321_000}, "reasoning": "model prose"}
    FinancialAnalystAgent._merge_sourced_facts(analysis, FILINGS)
    assert analysis["reasoning"] == "model prose"
    assert analysis["revenue_analysis"]["quality_assessment"] == "High-quality recurring revenue."
    assert analysis["revenue_analysis"]["annual_revenue"] == 457.5e6          # deterministic number wins
    assert [p["fiscal_year"] for p in analysis["historical_financials"]] == ["FY2024", "FY2025"]
    assert {s["url"] for s in analysis["sources"]} == {"https://www.sec.gov/a", "https://www.sec.gov/b"}
    assert analysis["cash_flow"]["free_cash_flow"] == 39_321_000              # not erased

    untouched = {"reasoning": "x"}
    FinancialAnalystAgent._merge_sourced_facts(untouched, {"has_data": False})
    FinancialAnalystAgent._merge_sourced_facts(untouched, None)
    assert untouched == {"reasoning": "x"}


def test_tools_used_is_recorded_with_outcomes():
    from app.main import _with_tools_used

    result = SimpleNamespace(data={"a": 1}, tool_calls=[
        {"name": "finnhub_data", "args": {}, "success": True},
        {"name": "web_search", "args": {}, "success": False, "error": "no results"}])
    data = _with_tools_used(result)
    assert data["tools_used"] == [{"name": "finnhub_data", "success": True},
                                  {"name": "web_search", "success": False, "error": "no results"}]
    assert _with_tools_used(SimpleNamespace(data={"a": 1}, tool_calls=None)) == {"a": 1}


def test_lbo_entry_far_above_market_is_flagged_and_key_variants_are_read():
    lbo = {"lbo_analysis_summary": {"scenario_analysis_lbo": [{"entry_ev_m": 1350}, {"entry_ev_m": 1500}, {"entry_ev_m": 1650}]}}
    issues = reconcile([{"agent_type": "advanced_financial_modeler", "data": lbo}], market_ev=582.3e6)
    assert len(issues) == 1 and "132%" in issues[0]["message"] and "not a credible" in issues[0]["message"]
    plausible = {"lbo_analysis_summary": {"scenario_analysis_lbo": [{"entry_ev_m": 700}, {"entry_ev_m": 800}]}}
    assert reconcile([{"agent_type": "advanced_financial_modeler", "data": plausible}], market_ev=582.3e6) == []


def test_enterprise_value_ratios_are_not_mistaken_for_values():
    from app.core.validation.reconciliation import extract_figures

    data = {"valuation_bridge": {"enterprise_value": "$1,695.6M", "ev_to_revenue": 3.7, "ev_ebitda_multiple": 14.0}}
    figures = extract_figures([{"agent_type": "dcf_lbo_architect", "data": data}])
    assert [round(e["value"] / 1e6, 1) for e in figures["dcf_ev"]] == [1695.6]
