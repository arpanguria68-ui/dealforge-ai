"""Turn chat-run agent results into the deal fields the report generators chart.

The PPTX/PDF/DOCX generators draw the football field from ``deal["valuation_output"]`` and
the revenue chart from ``deal["fact_base"]["metrics"]["historical_revenue"]``. Only the
background orchestrator fills those; a run started from the chat UI leaves them empty, so
the valuation and growth visuals never appeared. These helpers derive them from what the
agents recorded, and never invent a figure the agents did not produce.
"""

from statistics import median, quantiles
from typing import Any, Dict, List, Optional, Tuple

TERMINAL_GROWTH = 0.025  # matches the valuation agent's DCF call


def _latest(agent_results: List[Dict[str, Any]], agent: str, needs: str) -> Dict[str, Any]:
    for result in reversed(agent_results or []):
        data = result.get("data") if isinstance(result, dict) else None
        if result.get("agent_type") == agent and isinstance(data, dict) and data.get(needs):
            return data
    return {}


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _dcf_ev(flows: List[float], wacc: float, growth: float = TERMINAL_GROWTH) -> Optional[float]:
    if not flows or wacc <= growth:
        return None
    pv = sum(cf / (1 + wacc) ** t for t, cf in enumerate(flows, 1))
    terminal = flows[-1] * (1 + growth) / (wacc - growth)
    return pv + terminal / (1 + wacc) ** len(flows)


def valuation_rows(agent_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Football-field rows in $B from the valuation agent: DCF (WACC +/-1pt) and peer comps (IQR)."""
    data = _latest(agent_results, "valuation_agent", "valuations")
    if not data:
        return []
    valuations = data.get("valuations") or {}
    audit = data.get("audit") or {}
    inputs = audit.get("inputs") or {}
    fin = inputs.get("financial_data") or {}
    market = inputs.get("market_data") or {}
    rows: List[Dict[str, Any]] = []

    dcf_value = _num((valuations.get("dcf") or {}).get("value"))
    wacc = _num(fin.get("wacc"))
    flows = [f for f in (fin.get("projected_cash_flows") or []) if _num(f) is not None]
    if dcf_value:
        low = high = dcf_value
        if wacc and flows:
            base = _dcf_ev(flows, wacc)
            hi_v, lo_v = _dcf_ev(flows, wacc - 0.01), _dcf_ev(flows, wacc + 0.01)
            if base and hi_v and lo_v:  # scale the recorded value by the model's sensitivity
                low, high = dcf_value * lo_v / base, dcf_value * hi_v / base
        label = f"DCF (WACC {wacc * 100:.1f}% +/-1pt)" if wacc else "DCF"
        rows.append({"method": label, "low": low / 1e9, "mid": dcf_value / 1e9, "high": high / 1e9})

    revenue = _num(fin.get("revenue"))
    multiples = sorted(m for m in (_num(p.get("ev_revenue")) for p in market.get("peers") or []) if m)
    if revenue and multiples:
        if len(multiples) >= 4:
            q1, _, q3 = quantiles(multiples, n=4)
        else:
            q1, q3 = multiples[0], multiples[-1]
        rows.append({
            "method": f"Trading comps ({len(multiples)} peers, EV/Revenue)",
            "low": revenue * q1 / 1e9, "mid": revenue * median(multiples) / 1e9, "high": revenue * q3 / 1e9,
        })
    return rows


def revenue_history(agent_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{year, amount ($M)}] from the financial analyst's source-linked historicals."""
    data = _latest(agent_results, "financial_analyst", "historical_financials")
    points = []
    for period in data.get("historical_financials") or []:
        if not isinstance(period, dict):
            continue
        revenue = _num(period.get("revenue"))
        year = period.get("fiscal_year") or period.get("period")
        if revenue and year:
            points.append({"year": str(year), "amount": revenue / 1e6})
    return sorted(points, key=lambda p: p["year"])


def revenue_bridge(history: List[Dict[str, Any]]) -> Tuple[List[str], List[float]]:
    """Waterfall input: first year, year-on-year changes, last year as the total bar."""
    if len(history) < 2:
        return [], []
    amounts = [float(p.get("amount") or 0) for p in history]
    labels = [str(history[0].get("year"))]
    values = [amounts[0]]
    for prev, cur, point in zip(amounts, amounts[1:], history[1:]):
        labels.append(f"+{point.get('year')}")
        values.append(cur - prev)
    labels.append(f"{history[-1].get('year')} total")
    values.append(amounts[-1])
    return labels, values


def valuation_references(agent_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Vertical reference lines for the football field ($B): current market EV and the LBO entry."""
    from app.core.validation.reconciliation import extract_figures

    figures = extract_figures(agent_results)
    lines: List[Dict[str, Any]] = []
    if figures["market_ev"]:
        lines.append({"label": "Market EV", "value": figures["market_ev"][0]["value"] / 1e9})
    if figures["lbo_entry_ev"]:
        entry = min(figures["lbo_entry_ev"], key=lambda e: e["value"])["value"] / 1e9
        lines.append({"label": "LBO entry", "value": entry})
    return lines


def enrich_deal_from_results(deal: Dict[str, Any], agent_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Copy of ``deal`` with chartable valuation and revenue data filled in when missing."""
    out = dict(deal or {})
    existing = (out.get("valuation_output") or {}).get("valuations")
    if not existing:
        rows = valuation_rows(agent_results)
        if rows:
            out["valuation_output"] = {
                **(out.get("valuation_output") or {}), "valuations": rows, "unit": "$B",
                "reference_lines": valuation_references(agent_results),
            }
    fact_base = dict(out.get("fact_base") or {})
    metrics = dict(fact_base.get("metrics") or {})
    if not metrics.get("historical_revenue"):
        history = revenue_history(agent_results)
        if len(history) >= 2:
            metrics["historical_revenue"] = history
            fact_base["metrics"] = metrics
            out["fact_base"] = fact_base
    return out
