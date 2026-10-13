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

import re
from statistics import median
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional

_SYMBOL = re.compile(r"[A-Z][A-Z.\-]{0,5}")


def normalize_ticker(value: Any) -> str:
    """"NASDAQ: SPT", "(NYSE:SPT)", "spt" -> "SPT"; "" when the value is not a ticker symbol."""
    text = re.sub(r"[()\s]", "", str(value or "")).upper()
    text = re.sub(r"^[A-Z]+:", "", text)  # drop an exchange prefix
    return text if _SYMBOL.fullmatch(text) else ""


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


_MONEY_KEYS = frozenset({
    "revenue", "annual_revenue", "free_cash_flow", "operating_cash_flow", "operating_income", "net_income",
    "ebitda", "gross_profit", "capital_expenditures", "cash", "long_term_debt", "total_debt",
})


def _unit_scale(hint: Any) -> float:
    text = str(hint or "").lower()
    if "billion" in text or re.search(r"bn|\$b|usd_b", text):
        return 1e9
    if "million" in text or re.search(r"mm|\$m|usd_m|m", text):
        return 1e6
    if "thousand" in text or re.search(r"\$k|usd_k", text):
        return 1e3
    return 1.0


def normalize_units(node: Any, scale: float = 1.0) -> Any:
    """Copy with money figures converted to raw dollars where a ``unit`` says millions etc.

    Agents often write ``{"unit": "millions USD", "years": [{"revenue": 405}]}``; reading
    405 as dollars would make every downstream valuation off by a million.
    """
    if isinstance(node, list):
        return [normalize_units(item, scale) for item in node]
    if not isinstance(node, dict):
        return node
    own = None
    for key in ("unit", "units", "currency_unit"):
        if isinstance(node.get(key), str):
            own = _unit_scale(node[key])
    local = own if own and own != 1.0 else scale
    out: Dict[str, Any] = {}
    for key, value in node.items():
        lowered = str(key).lower()
        child_scale = local
        if isinstance(value, (dict, list)):  # "historical_financials_millions_usd": {...}
            named = 1e9 if "billions" in lowered else 1e6 if "millions" in lowered else 1e3 if "thousands" in lowered else None
            child_scale = named or local
        key_scale = local
        if lowered.endswith(("_m", "_millions", "_usd_m")):
            key_scale = 1e6
        if isinstance(value, (int, float)) and not isinstance(value, bool) and (
            lowered in _MONEY_KEYS or lowered.rstrip("m_") in _MONEY_KEYS
        ) and key_scale != 1.0:
            out[key] = float(value) * key_scale
        else:
            out[key] = normalize_units(value, child_scale)
    return out


def _latest_year_value(outputs: Dict[str, Any], key: str) -> Optional[float]:
    """Newest fiscal-year value of ``key`` from year-by-year records (FY2025 beats FY2024)."""
    best: Optional[tuple] = None
    for data in outputs.values():
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                year = node.get("fiscal_year") or node.get("year")
                number = _num(node.get(key))
                digits = re.search(r"\d{4}", str(year)) if year is not None else None
                if number is not None and digits:
                    candidate = (int(digits.group(0)), number)
                    if best is None or candidate[0] > best[0]:
                        best = candidate
                for child_key, child in node.items():  # {"2024": {"revenue": ...}} keyed by year
                    if isinstance(child, dict) and re.fullmatch(r"(?:FY)?(\d{4})", str(child_key)):
                        child_number = _num(child.get(key))
                        if child_number is not None:
                            candidate = (int(re.search(r"\d{4}", str(child_key)).group(0)), child_number)
                            if best is None or candidate[0] > best[0]:
                                best = candidate
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return best[1] if best else None


def derive_valuation_inputs(prior_outputs: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map earlier agents' explicit outputs to ``financial_data``/``market_data``."""
    outputs = {k: normalize_units(v) for k, v in (prior_outputs or {}).items() if isinstance(v, dict)}
    financial: Dict[str, Any] = {}
    market: Dict[str, Any] = {}

    revenue = _first_number(outputs, ("annual_revenue",)) or _latest_year_value(outputs, "revenue")
    if revenue:
        financial["revenue"] = revenue
    fcf = _first_number(outputs, ("free_cash_flow",))
    latest_fcf = _latest_year_value(outputs, "free_cash_flow")
    if latest_fcf is not None:
        fcf = latest_fcf  # the newest fiscal year, not whichever entry came first
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


MODELLING_AGENTS = frozenset({"dcf_lbo_architect", "advanced_financial_modeler", "valuation_agent"})


def shared_assumptions_note(results: List[Dict[str, Any]]) -> str:
    """One assumption set for every agent that builds a DCF/LBO, taken from the valuation agent.

    Agents otherwise pick their own WACC and growth and their enterprise values disagree
    by 30%+. Returns "" until the valuation agent has recorded its inputs.
    """
    for result in results or []:
        data = result.get("data") if isinstance(result, dict) else None
        audit = data.get("audit") if isinstance(data, dict) and isinstance(data.get("audit"), dict) else None
        inputs = ((audit or {}).get("inputs") or {}).get("financial_data") or {}
        wacc = _num(inputs.get("wacc"))
        if not wacc:
            continue
        lines = [f"- WACC {wacc:.2%}",
                 f"- terminal growth {DEFAULT_ASSUMPTIONS['terminal_growth']:.1%}",
                 f"- projection horizon {int(DEFAULT_ASSUMPTIONS['projection_years'])} years"]
        growth, fcf = _num(inputs.get("revenue_growth")), _num(inputs.get("free_cash_flow"))
        if growth is not None:
            lines.append(f"- starting revenue growth {growth:.1%}")
        if fcf:
            lines.append(f"- base free cash flow ${fcf:,.0f}")
        market = ((audit or {}).get("inputs") or {}).get("market_data") or {}
        if _num(market.get("enterprise_value")):
            lines.append(f"- current market enterprise value ${_num(market['enterprise_value']):,.0f} "
                         "(a take-private entry must sit above it)")
        return ("SHARED VALUATION ASSUMPTIONS (use these; if you deviate, state the new value and why, "
                "so the DCFs stay comparable):\n" + "\n".join(lines))
    return ""


PEER_SIZE_BAND = (0.1, 10.0)  # peer market cap relative to the target's


def curate_peers(rows: List[Dict[str, Any]], target_cap: Optional[float]) -> tuple:
    """Keep peers of comparable size; Finnhub's list mixes in unrelated conglomerates.

    Returns ``(kept, dropped_tickers)``. If the filter would leave fewer than three
    peers (or the target's cap is unknown) the full list is kept, so a thin peer set
    never silently becomes none.
    """
    if not target_cap or target_cap <= 0:
        return rows, []
    low, high = target_cap * PEER_SIZE_BAND[0], target_cap * PEER_SIZE_BAND[1]
    kept = [r for r in rows if r.get("market_cap") and low <= r["market_cap"] <= high]
    if len(kept) < 3:
        return rows, []
    return kept, [r["ticker"] for r in rows if r not in kept]


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
        target_cap = _num(target.get("marketCapitalization"))  # $M
        for symbol in [p for p in (peers or []) if p and p != ticker][: MAX_PEERS * 3]:
            metrics = metric_of(await fetch("metric", symbol))
            multiple = _num(metrics.get("evRevenueTTM"))
            if multiple and 0 < multiple <= MAX_SANE_EV_REVENUE:
                peer_rows.append({"ticker": symbol, "ev_revenue": multiple,
                                  "ev_fcf": _num(metrics.get("currentEv/freeCashFlowTTM")),
                                  "market_cap": _num(metrics.get("marketCapitalization"))})
        peer_rows, dropped = curate_peers(peer_rows, target_cap)
        peer_rows = peer_rows[:MAX_PEERS]
        if dropped:
            out["assumptions"].append(
                f"peer set trimmed to comparable size (0.1x-10x market cap); excluded {', '.join(dropped)}"
            )
        if peer_rows:
            out["market_data"]["ev_revenue_median"] = round(median(p["ev_revenue"] for p in peer_rows), 4)
            out["market_data"]["peers"] = peer_rows
            out["sources"].append(f"Finnhub stock/peers + stock/metric ({len(peer_rows)} peers)")
        else:
            out["missing"].append("peer EV/Revenue multiples")

    beta = _num(target.get("beta"))
    market_cap = _num(target.get("marketCapitalization"))  # $M
    market_ev = _num(target.get("enterpriseValue"))  # $M
    if market_ev:
        out["market_data"]["enterprise_value"] = market_ev * 1e6
    if market_cap:
        out["market_data"]["market_cap"] = market_cap * 1e6
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
