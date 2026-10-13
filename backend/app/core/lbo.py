"""Deterministic take-private LBO built from sourced inputs.

Used when a requested LBO is missing from the architect agent's output, so the report never
silently lacks it and every figure is reproducible. All inputs are either sourced (market
enterprise value, free cash flow, growth from the valuation agent) or stated assumptions
returned alongside the result; nothing is invented.

Conventions: entry at a premium to the current market enterprise value; exit at the entry
EV/FCF multiple (no multiple expansion); cash flow after after-tax interest sweeps the debt.
All values in dollars.
"""

from typing import Any, Dict, List, Optional

from app.core.valuation_inputs import project_cash_flows

DEFAULT_LBO_ASSUMPTIONS = {
    "premium": 0.30,        # take-private premium to market enterprise value
    "debt_pct": 0.40,       # share of entry EV funded with debt
    "interest_rate": 0.09,  # blended cost of LBO debt
    "tax_rate": 0.25,
    "sweep": 0.75,          # share of post-interest cash flow used to repay debt
    "holding_years": 5,
}


def build_lbo(
    market_ev: float,
    base_fcf: float,
    growth: float,
    terminal_growth: float,
    assumptions: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Entry, financing, debt paydown and sponsor returns; ``None`` when inputs cannot support one."""
    a = {**DEFAULT_LBO_ASSUMPTIONS, **(assumptions or {})}
    if not (market_ev and market_ev > 0 and base_fcf and base_fcf > 0 and growth is not None):
        return None
    years = int(a["holding_years"])
    flows = project_cash_flows(base_fcf, growth, terminal_growth, years)

    entry_ev = market_ev * (1 + a["premium"])
    debt = entry_ev * a["debt_pct"]
    equity = entry_ev - debt
    schedule: List[Dict[str, float]] = []
    remaining = debt
    for year, fcf in enumerate(flows, start=1):
        interest = remaining * a["interest_rate"]
        after_tax_interest = interest * (1 - a["tax_rate"])
        repayment = min(remaining, max(0.0, (fcf - after_tax_interest) * a["sweep"]))
        remaining -= repayment
        schedule.append({
            "year": year, "free_cash_flow": round(fcf), "interest": round(interest),
            "debt_repaid": round(repayment), "debt_remaining": round(remaining),
        })

    entry_multiple = entry_ev / base_fcf
    exit_ev = flows[-1] * entry_multiple
    exit_equity = exit_ev - remaining
    moic = exit_equity / equity if equity > 0 else 0.0
    irr = moic ** (1 / years) - 1 if moic > 0 else -1.0
    return {
        "source": "deterministic build from sourced inputs; assumptions listed below",
        "entry_valuation": round(entry_ev),
        "market_enterprise_value": round(market_ev),
        "premium_to_market": a["premium"],
        "sources_and_uses": {
            "uses_purchase_enterprise_value": round(entry_ev),
            "sources_debt": round(debt), "sources_sponsor_equity": round(equity),
        },
        "debt_schedule": schedule,
        "exit": {"year": years, "enterprise_value": round(exit_ev), "ev_to_fcf_multiple": round(entry_multiple, 1),
                 "net_debt": round(remaining), "equity_value": round(exit_equity)},
        "returns": {"moic": round(moic, 2), "irr": round(irr, 4)},
        "assumptions": [
            f"entry at a {a['premium']:.0%} premium to current market enterprise value",
            f"{a['debt_pct']:.0%} debt at {a['interest_rate']:.1%}, tax rate {a['tax_rate']:.0%}",
            f"{a['sweep']:.0%} of cash flow after interest repays debt",
            "exit at the entry EV/FCF multiple (no multiple expansion)",
            f"free cash flow grows from {growth:.1%} fading to {terminal_growth:.1%} over {years} years",
        ],
    }


def deterministic_dcf(
    base_fcf: float, growth: float, wacc: float, terminal_growth: float, years: int = 5
) -> Optional[Dict[str, Any]]:
    """Unlevered DCF bridge in dollars: PV of projected cash flows + PV of terminal value."""
    if not (base_fcf and base_fcf > 0 and growth is not None and wacc and wacc > terminal_growth):
        return None
    flows = project_cash_flows(base_fcf, growth, terminal_growth, years)
    pv_flows = sum(flow / (1 + wacc) ** t for t, flow in enumerate(flows, start=1))
    terminal_value = flows[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / (1 + wacc) ** years
    return {
        "pv_projected_fcfs": round(pv_flows),
        "pv_terminal_value": round(pv_terminal),
        "enterprise_value": round(pv_flows + pv_terminal),
        "wacc": wacc,
        "terminal_growth": terminal_growth,
        "projection_years": years,
        "unit": "USD",
        "note": "Recomputed from the shared valuation assumptions because the stated bridge did not add up.",
    }
