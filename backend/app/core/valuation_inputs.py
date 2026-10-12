"""Inputs for ValuationAgent: prior task outputs + sourced market data.

Practice followed (sell-side / PE standard):
* Comparable multiples come from a peer set of listed companies, summarised by
  the *median* (robust to outliers), with the peer list and source recorded.
* WACC is built with CAPM: Ke = rf + beta * ERP (+ size premium), blended with
  an after-tax cost of debt at market-value weights.
* Cash flows are projected from the last reported FCF with growth fading
  linearly toward the terminal rate.

Observed data (revenue, FCF, beta, multiples) is taken from upstream agents or
Finnhub. Everything else (risk-free rate, ERP, size premium, cost of debt, tax,
growth fade) is an explicit *assumption*, returned in ``assumptions`` so the
reader can audit and override it. Nothing is silently invented: if a required
observed input is missing the method is skipped and listed in ``missing``.
"""

from statistics import median
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional

_MULTIPLE_KEYS = ("ev_revenue_multiple", "ev_revenue", "ev_to_revenue")
MAX_PEERS = 6
MAX_SANE_EV_REVENUE = 30.0  # drops data errors / hyper-valuation outliers

# Overridable assumptions (documented defaults, not observed data).
DEFAULT_ASSUMPTIONS: Dict[str, float] = {
    "risk_free_rate": 0.0425,
    "equity_risk_premium": 0.050,
    "small_cap_premium": 0.010,  # applied when market cap < $2B
    "pre_tax_cost_of_debt": 0.070,
    "tax_rate": 0.25,
    "terminal_growth": 0.025,
    "projection_years": 5,
}

Fetch = Callable[[str, str], Awaitable[Any]]


def _walk(node: Any) -> Iterator[tuple]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key).casefold(), value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _first_number(outputs: Dict[str, Any], keys: tuple) -> Optional[float]:
    for data in outputs.values():
        for key, value in _walk(data):
            if key in keys and (number := _num(value)) is not None:
                return number
    return None


def derive_valuation_inputs(prior_outputs: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map earlier agents' explicit outputs to ``financial_data``/``market_data``."""
    outputs = {k: v for k, v in (prior_outputs or {}).items() if isinstance(v, dict)}
    financial: Dict[str, Any] = {}
    market: Dict[str, Any] = {}

    revenue = _first_number(outputs, ("annual_revenue",))
    if revenue:
        financial["revenue"] = revenue
    fcf = _first_number(outputs, ("free_cash_flow",))
    if fcf is not None:
        financial["free_cash_flow"] = fcf
    for data in outputs.values():
        growth = None
        if isinstance(data.get("revenue_analysis"), dict):
            growth = _num(data["revenue_analysis"].get("growth_rate"))
        if growth is not None:
            financial["revenue_growth"] = growth / 100 if abs(growth) > 1 else growth
            break

    wacc = _first_number(outputs, ("wacc", "discount_rate"))
    if wacc:
        financial["wacc"] = wacc / 100 if wacc > 1 else wacc

    for data in outputs.values():
        for key, value in _walk(data):
            if key in ("projected_cash_flows", "free_cash_flow_projections") and isinstance(value, list):
                flows = [n for n in (_num(v) for v in value) if n is not None]
                if flows:
                    financial["projected_cash_flows"] = flows
                    break
        if "projected_cash_flows" in financial:
            break

    explicit = _first_number(outputs, ("ev_revenue_median",))
    if explicit:
        market["ev_revenue_median"] = explicit
    else:
        multiples: List[float] = []
        for data in outputs.values():
            for _, value in _walk(data):
                if isinstance(value, dict):
                    for mkey in _MULTIPLE_KEYS:
                        number = _num(value.get(mkey))
                        if number and number > 0:
                            multiples.append(number)
                            break
        if multiples:
            market["ev_revenue_median"] = median(multiples)

    return {"financial_data": financial, "market_data": market}


def build_wacc(
    beta: float, market_cap: float, debt: float, assumptions: Dict[str, float]
) -> Dict[str, Any]:
    """CAPM cost of equity blended with after-tax debt at market-value weights."""
    a = {**DEFAULT_ASSUMPTIONS, **(assumptions or {})}
    size = a["small_cap_premium"] if market_cap < 2_000 else 0.0  # market_cap in $M
    ke = a["risk_free_rate"] + beta * a["equity_risk_premium"] + size
    kd = a["pre_tax_cost_of_debt"] * (1 - a["tax_rate"])
    total = market_cap + max(debt, 0.0)
    we = market_cap / total if total > 0 else 1.0
    wacc = we * ke + (1 - we) * kd
    return {
        "wacc": round(wacc, 4),
        "cost_of_equity": round(ke, 4),
        "after_tax_cost_of_debt": round(kd, 4),
        "equity_weight": round(we, 4),
        "beta": beta,
    }


def project_cash_flows(
    base_fcf: float, growth: float, terminal_growth: float, years: int
) -> List[float]:
    """Grow FCF from ``growth`` fading linearly to ``terminal_growth``."""
    flows, current = [], base_fcf
    for t in range(1, years + 1):
        g = growth + (terminal_growth - growth) * (t - 1) / max(years - 1, 1)
        current *= 1 + g
        flows.append(round(current, 2))
    return flows


async def gather_market_inputs(
    ticker: str, fetch: Fetch, financial_data: Dict[str, Any],
    assumptions: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Pull sourced peer multiples + beta and derive WACC / projected cash flows.

    ``fetch(endpoint, symbol)`` returns the Finnhub JSON payload (or None).
    Returns ``financial_data``/``market_data`` additions plus ``assumptions``,
    ``sources`` and ``missing`` for the audit trail.
    """
    a = {**DEFAULT_ASSUMPTIONS, **(assumptions or {})}
    out: Dict[str, Any] = {
        "financial_data": {}, "market_data": {}, "assumptions": [], "sources": [], "missing": [],
    }
    metric_of = lambda payload: (payload or {}).get("metric") or {}  # noqa: E731

    target = metric_of(await fetch("metric", ticker))
    if not target:
        out["missing"].append(f"Finnhub metrics for {ticker}")
    else:
        out["sources"].append(f"Finnhub stock/metric {ticker}")

    if "ev_revenue_median" not in out["market_data"]:
        peers = await fetch("peers", ticker)
        peer_rows = []
        for symbol in [p for p in (peers or []) if p and p != ticker][: MAX_PEERS * 2]:
            metrics = metric_of(await fetch("metric", symbol))
            multiple = _num(metrics.get("evRevenueTTM"))
            if multiple and 0 < multiple <= MAX_SANE_EV_REVENUE:
                peer_rows.append({"ticker": symbol, "ev_revenue": multiple,
                                  "ev_fcf": _num(metrics.get("currentEv/freeCashFlowTTM"))})
            if len(peer_rows) >= MAX_PEERS:
                break
        if peer_rows:
            out["market_data"]["ev_revenue_median"] = round(median(p["ev_revenue"] for p in peer_rows), 4)
            out["market_data"]["peers"] = peer_rows
            out["sources"].append(f"Finnhub stock/peers + stock/metric ({len(peer_rows)} peers)")
        else:
            out["missing"].append("peer EV/Revenue multiples")

    beta = _num(target.get("beta"))
    market_cap = _num(target.get("marketCapitalization"))  # $M
    if beta is not None and market_cap:
        debt_ratio = _num(target.get("totalDebt/totalEquityQuarterly")) or 0.0
        wacc = build_wacc(beta, market_cap, debt_ratio * market_cap, a)
        out["financial_data"]["wacc"] = wacc["wacc"]
        out["wacc_build"] = wacc
        out["assumptions"] += [
            f"risk-free rate {a['risk_free_rate']:.2%}", f"equity risk premium {a['equity_risk_premium']:.2%}",
            f"pre-tax cost of debt {a['pre_tax_cost_of_debt']:.2%}", f"tax rate {a['tax_rate']:.0%}",
        ]
        if market_cap < 2_000:
            out["assumptions"].append(f"small-cap premium {a['small_cap_premium']:.2%}")
    else:
        out["missing"].append("beta / market cap for WACC")

    base_fcf = _num(financial_data.get("free_cash_flow"))
    growth = _num(financial_data.get("revenue_growth"))
    if growth is None:
        growth = _num(target.get("revenueGrowthTTMYoy"))
        growth = growth / 100 if growth is not None else None
    if base_fcf and base_fcf > 0 and growth is not None:
        out["financial_data"]["projected_cash_flows"] = project_cash_flows(
            base_fcf, growth, a["terminal_growth"], int(a["projection_years"])
        )
        out["assumptions"].append(
            f"FCF grows from {growth:.1%} fading linearly to {a['terminal_growth']:.1%} over "
            f"{int(a['projection_years'])} years"
        )
    else:
        out["missing"].append("positive base FCF and revenue growth for cash-flow projection")
    return out
