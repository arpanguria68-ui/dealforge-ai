from app.agents.dcf_lbo_architect import DCFLBOArchitectAgent
from app.core.lbo import build_lbo
from app.core.validation.reconciliation import reconcile

VAL = {"agent_type": "valuation_agent", "data": {"audit": {"inputs": {
    "financial_data": {"free_cash_flow": 39_321_000, "revenue_growth": 0.127},
    "market_data": {"enterprise_value": 582_253_700}}}}}


def test_lbo_entry_is_above_market_and_arithmetic_holds():
    lbo = build_lbo(582_253_700, 39_321_000, 0.127, 0.025)
    assert lbo["entry_valuation"] == round(582_253_700 * 1.3)
    uses = lbo["sources_and_uses"]
    assert uses["sources_debt"] + uses["sources_sponsor_equity"] == uses["uses_purchase_enterprise_value"] or \
        abs(uses["sources_debt"] + uses["sources_sponsor_equity"] - uses["uses_purchase_enterprise_value"]) <= 1
    last = lbo["debt_schedule"][-1]
    assert last["debt_remaining"] == lbo["exit"]["net_debt"] and last["debt_remaining"] < uses["sources_debt"]
    assert lbo["exit"]["equity_value"] == lbo["exit"]["enterprise_value"] - lbo["exit"]["net_debt"]
    assert lbo["returns"]["moic"] > 0 and lbo["assumptions"]


def test_lbo_needs_sourced_inputs():
    assert build_lbo(None, 39e6, 0.1, 0.025) is None
    assert build_lbo(582e6, 0, 0.1, 0.025) is None
    assert build_lbo(582e6, -5e6, 0.1, 0.025) is None


def test_architect_adds_missing_lbo_and_reconciliation_accepts_it():
    agent = DCFLBOArchitectAgent()
    analysis = {"dcf_valuation_model": {"enterprise_value": 829e6}}
    agent._ensure_lbo("Build a DCF and LBO", {"agent_results": [VAL]}, analysis)
    assert analysis["lbo_model"]["entry_valuation"] > 582_253_700
    assert reconcile([VAL, {"agent_type": "dcf_lbo_architect", "data": analysis}]) == [] or \
        all(i["metric"] != "LBO entry vs market value" for i in reconcile([VAL, {"agent_type": "dcf_lbo_architect", "data": analysis}]))


def test_architect_does_not_override_or_invent():
    agent = DCFLBOArchitectAgent()
    own = {"lbo_model": {"entry_valuation": 1}}
    agent._ensure_lbo("LBO", {"agent_results": [VAL]}, own)
    assert own["lbo_model"] == {"entry_valuation": 1}
    none_requested = {}
    agent._ensure_lbo("Build a DCF", {"agent_results": [VAL]}, none_requested)
    assert none_requested == {}
    unsourced = {}
    agent._ensure_lbo("Build an LBO", {"agent_results": []}, unsourced)
    assert "could not be built" in unsourced["data_limitations"][0]


def test_inconsistent_dcf_bridge_is_recomputed_from_shared_inputs():
    from app.core.lbo import deterministic_dcf

    valuation = {"agent_type": "valuation_agent", "data": {"audit": {"inputs": {
        "financial_data": {"free_cash_flow": 39_321_000, "revenue_growth": 0.127, "wacc": 0.0958},
        "market_data": {"enterprise_value": 582_253_700}}}}}
    analysis = {"unit": "Millions USD", "dcf_model": {"valuation_bridge": {
        "pv_fcfs_5y": "$1,107.0", "pv_terminal_value": "$1,030.0", "enterprise_value": "$829.0"}}}
    DCFLBOArchitectAgent()._repair_dcf_bridge({"agent_results": [valuation]}, analysis)
    bridge = analysis["dcf_model"]["valuation_bridge"]
    assert bridge["pv_projected_fcfs"] + bridge["pv_terminal_value"] == bridge["enterprise_value"]
    assert abs(bridge["enterprise_value"] - 713_270_269) < 5
    assert analysis["data_limitations"]
    assert reconcile([valuation, {"agent_type": "dcf_lbo_architect", "data": analysis}], market_ev=582_253_700) == []
    assert deterministic_dcf(0, 0.1, 0.09, 0.025) is None


def test_consistent_bridge_is_left_alone():
    analysis = {"unit": "Millions USD", "dcf_model": {"valuation_bridge": {
        "pv_fcfs_5y": "$200.0", "pv_terminal_value": "$500.0", "enterprise_value": "$700.0"}}}
    before = repr(analysis)
    DCFLBOArchitectAgent()._repair_dcf_bridge({"agent_results": []}, analysis)
    assert repr(analysis) == before
