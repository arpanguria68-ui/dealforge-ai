from app.main import summarize_deal_insights


def test_insights_report_valuation_tools_and_flags_only_recorded_values():
    results = [
        {"agent_type": "valuation_agent", "data": {
            "valuations": {"dcf": {"value": 713e6}, "comparable_companies": {"value": 1.2e9}},
            "weighted_estimate": 9.4e8,
            "audit": {"inputs": {"market_data": {"enterprise_value": 582e6}}},
            "tools_used": [{"name": "finnhub_data", "success": True}, {"name": "peer_discovery", "success": False}]}},
        {"agent_type": "dcf_lbo_architect", "data": {
            "unit": "Millions USD", "lbo_model": {"deal_structure": {"entry_valuation": "$420.0"}}}},
        {"agent_type": "investment_memo_agent", "data": {"grounding": {"figures_grounded": 55, "figures_total": 58}}},
    ]
    insights = summarize_deal_insights(results)
    assert insights["valuation"] == {"dcf": 713e6, "comps": 1.2e9, "weighted": 9.4e8}
    assert insights["tools"] == {"ok": 1, "failed": 1}
    assert insights["grounding"] == {"grounded": 55, "total": 58}
    assert insights["needs_review"] is True            # LBO enters below market value
    assert "below the market" in insights["reconciliation"][0]


def test_insights_empty_when_nothing_recorded():
    assert summarize_deal_insights([{"agent_type": "risk_assessor", "data": {"risks": []}}]) == {}
