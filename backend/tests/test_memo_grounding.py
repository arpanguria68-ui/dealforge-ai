import asyncio

import pytest

from app.agents.investment_memo_agent import _verified_valuation_block
from app.core.reflection.reflection_engine import ReflectionEngine
from app.core.validation.memo_grounding import check_memo_grounding, grounding_notice

RESULTS = [
    {"agent": "financial_analyst", "data": {
        "revenue_analysis": {"annual_revenue": 457547000, "growth_rate": 12.72},
        "cash_flow": {"operating_cash_flow": 43427000, "free_cash_flow": 39321000},
        "profitability": {"gross_margin": 77.56}}},
    {"agent": "valuation_agent", "data": {"audit": {
        "sources": ["Finnhub stock/metric SPT"], "assumptions": ["risk-free rate 4.25%"], "missing": [],
        "wacc_build": {"wacc": 0.0958},
        "inputs": {"market_data": {"ev_revenue_median": 2.594,
                                    "peers": [{"ticker": "DSP", "ev_revenue": 1.75}]}}}}},
]


def test_sourced_figures_are_grounded_across_units():
    memo = "FY2025 revenue was $457.55M (up 12.72%) with gross margin 77.56%, FCF of $39.32M, peers at 2.59x."
    report = check_memo_grounding(memo, RESULTS)
    assert report["status"] == "ok" and report["ungrounded"] == []


def test_invented_figures_and_vendors_are_flagged():
    memo = ("Operating cash flow was $47.5M and capex $8.18M; beta 1.35, WACC 10.5%, "
            "peers at 2.43x (Source: Bloomberg / S&P Capital IQ).")
    report = check_memo_grounding(memo, RESULTS)
    assert report["status"] == "low_grounding"
    assert {"$47.5M", "$8.18M", "2.43x"} <= set(report["ungrounded"])
    assert "bloomberg" in report["unsupported_sources"]
    assert "Data provenance warning" in grounding_notice(report)


def test_vendor_allowed_when_it_appears_in_evidence():
    results = RESULTS + [{"agent": "x", "data": {"source": "FactSet export", "revenue": 457547000}}]
    assert "factset" not in check_memo_grounding("Per FactSet, revenue $457.55M.", results)["unsupported_sources"]


def test_no_sources_is_reported_not_passed():
    assert check_memo_grounding("Revenue $1M", [])["status"] == "no_sources"


def test_verified_block_carries_valuation_audit():
    block = _verified_valuation_block(RESULTS)
    assert "VERIFIED VALUATION INPUTS" in block and "2.59x" in block and "risk-free rate 4.25%" in block
    assert _verified_valuation_block([{"agent": "x", "data": {}}]) == ""


def test_judge_accuracy_is_capped_by_grounding():
    low = {"grounding": {"status": "low_grounding", "grounded_ratio": 0.2, "unsupported_sources": []}}
    capped = ReflectionEngine._cap_accuracy_by_grounding({"accuracy": 0.96, "completeness": 0.98}, low)
    assert capped["accuracy"] == pytest.approx(0.52) and capped["completeness"] == 0.98
    vendors = {"grounding": {"status": "low_grounding", "grounded_ratio": 0.9, "unsupported_sources": ["bloomberg"]}}
    assert ReflectionEngine._cap_accuracy_by_grounding({"accuracy": 0.96}, vendors)["accuracy"] == 0.6
    assert ReflectionEngine._cap_accuracy_by_grounding({"accuracy": 0.96}, {})["accuracy"] == 0.96
    assert "DETERMINISTIC CHECKS" in ReflectionEngine._grounding_prompt(low)
