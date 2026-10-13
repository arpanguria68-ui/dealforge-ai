"""Cross-agent consistency checks for a deal analysis.

Each agent can be internally tidy while the set disagrees: one DCF says $700M, another
$785M, and an LBO "enters" at $270M for a company the market values at ~$580M. Nothing
compared them. This module extracts the headline enterprise-value figures from the agents'
recorded output and reports disagreements, so a reader (and the memo) sees them instead of
a confident-looking set of incompatible numbers.

Figures are parsed from numbers *and* formatted strings ("$270.0M"); a value is read as
dollars, or as $M when the key says so or it is implausibly small for dollars.
"""

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

DCF_DISAGREEMENT = 0.15      # two DCF enterprise values this far apart are flagged
ENTRY_BELOW_MARKET = 0.95    # entry EV below this share of market EV: no take-private premium
ENTRY_PREMIUM_LIMIT = 0.60   # entry more than 60% above market EV: far outside usual take-private premiums
_LBO_AGENTS = frozenset({"dcf_lbo_architect", "advanced_financial_modeler"})
_MONEY_STRING = re.compile(r"^\s*\$?\s*(-?\d[\d,]*\.?\d*)\s*(billion|bn|b|million|mm|m|thousand|k)?\s*$", re.I)
_SCALE = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mm": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


def parse_money(value: Any, key: str = "") -> Optional[float]:
    """Dollars from 270000000, "$270.0M", "$1.2B" or (when the key says $M) 270.0."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if re.search(r"(_m|_millions|usd_m|\(\$m\)|\bmm\b)$", key.lower()):
            return number * 1e6
        return number if abs(number) >= 1e6 else None   # small bare numbers: unit unknown, skip
    if isinstance(value, str):
        match = _MONEY_STRING.match(value)
        if not match:
            return None
        number = float(match.group(1).replace(",", ""))
        unit = (match.group(2) or "").lower()
        return number * _SCALE[unit] if unit else (number if abs(number) >= 1e6 else None)
    return None


def _walk(node: Any, path: str = "") -> Iterator[Tuple[str, str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, str(key), value
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]")


def _is_ev_key(key: str) -> bool:
    """Enterprise-value field names vary per run: entry_ev, entry_ev_m, enterprise_value, ev_usd_m ..."""
    tokens = set(re.split(r"[^a-z0-9]+", key.lower()))
    if "multiple" in tokens or "to" in tokens:  # ev/ebitda, ev_to_revenue are ratios, not values
        return False
    return "ev" in tokens or {"enterprise", "value"} <= tokens


def _is_entry_value_key(key: str) -> bool:
    """What the sponsor pays for the whole business: entry EV, purchase enterprise value, transaction value."""
    lowered = key.lower()
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    if "multiple" in tokens or "to" in tokens or "pct" in tokens or "percent" in tokens:
        return False
    if _is_ev_key(key) and ("entry" in tokens or "purchase" in tokens or "offer" in tokens or "transaction" in tokens):
        return True
    return bool({"purchase", "price"} <= tokens and "equity" not in tokens and "per" not in tokens
                or {"transaction", "value"} <= tokens or {"entry", "value"} <= tokens)


def extract_figures(agent_results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Headline figures by kind: ``dcf_ev``, ``lbo_entry_ev``, ``market_ev``."""
    found: Dict[str, List[Dict[str, Any]]] = {"dcf_ev": [], "lbo_entry_ev": [], "market_ev": []}
    for result in agent_results or []:
        data = result.get("data") if isinstance(result, dict) else None
        agent = str(result.get("agent_type", "agent")) if isinstance(result, dict) else "agent"
        if not isinstance(data, dict):
            continue
        for path, key, value in _walk(data):
            context = f"{path}/{key}".lower()
            money = parse_money(value, key)
            if money is None:
                continue
            entry = {"agent": agent, "value": money, "where": f"{path}/{key}".strip("/")}
            scenario = "bear" in context or "bull" in context
            if agent == "valuation_agent" and path == "/valuations/dcf" and key == "value":
                found["dcf_ev"].append(entry)
            elif not scenario and _is_entry_value_key(key) and (
                    "lbo" in context or "sponsor" in context or agent in _LBO_AGENTS):
                found["lbo_entry_ev"].append(entry)
            elif _is_ev_key(key) and not scenario and "entry" not in context and (
                    "dcf" in context or "valuation_bridge" in context or "intrinsic" in context):
                found["dcf_ev"].append(entry)
        audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
        market = ((audit.get("inputs") or {}).get("market_data") or {}) if audit else {}
        for field in ("enterprise_value", "market_ev"):
            if isinstance(market.get(field), (int, float)):
                found["market_ev"].append({"agent": agent, "value": float(market[field]), "where": f"audit.market_data.{field}"})
        # the Base column of an LBO summary table: {"Metric": ["Entry EV ($M)", ...], "Base": ["$270.0M", ...]}
        for _, key, table in _walk(data):
            if key.lower().endswith("summary_table") and isinstance(table, dict) and isinstance(table.get("Metric"), list):
                metrics = [str(m).lower() for m in table["Metric"]]
                base = table.get("Base")
                if isinstance(base, list) and "entry ev ($m)" in metrics:
                    money = parse_money(base[metrics.index("entry ev ($m)")], "entry_ev_m")
                    if money:
                        found["lbo_entry_ev"].append({"agent": agent, "value": money, "where": f"{key}.Base.Entry EV"})
    for kind, entries in found.items():  # one figure per (agent, value)
        seen, unique = set(), []
        for entry in entries:
            marker = (entry["agent"], round(entry["value"]))
            if marker not in seen:
                seen.add(marker)
                unique.append(entry)
        found[kind] = unique
    return found


def _fmt(value: float) -> str:
    return f"${value / 1e9:,.2f}B" if abs(value) >= 1e9 else f"${value / 1e6:,.1f}M"


def reconcile(agent_results: List[Dict[str, Any]], market_ev: Optional[float] = None) -> List[Dict[str, Any]]:
    """Issues found across agents; empty when the set is consistent (or too sparse to compare)."""
    figures = extract_figures(agent_results)
    if market_ev:
        figures["market_ev"].append({"agent": "market data", "value": float(market_ev), "where": "market"})
    issues: List[Dict[str, Any]] = []

    dcfs = figures["dcf_ev"]
    if len(dcfs) >= 2:
        low, high = min(dcfs, key=lambda e: e["value"]), max(dcfs, key=lambda e: e["value"])
        gap = (high["value"] - low["value"]) / low["value"] if low["value"] > 0 else 0
        if gap > DCF_DISAGREEMENT:
            issues.append({
                "metric": "DCF enterprise value", "severity": "high",
                "reports": [{"agent": e["agent"], "value": _fmt(e["value"])} for e in (low, high)],
                "message": f"Two DCFs differ by {gap:.0%} ({_fmt(low['value'])} vs {_fmt(high['value'])}). "
                           "Their WACC, growth and cash-flow assumptions must be reconciled.",
            })

    markets = figures["market_ev"]
    entries = figures["lbo_entry_ev"]
    if markets and entries:
        market = max(markets, key=lambda e: e["value"]) if len(markets) > 1 else markets[0]
        low, high = min(entries, key=lambda e: e["value"]), max(entries, key=lambda e: e["value"])
        if high["value"] < market["value"] * ENTRY_BELOW_MARKET:   # even the best case is a discount
            issues.append({
                "metric": "LBO entry vs market value", "severity": "high",
                "reports": [{"agent": high["agent"], "value": _fmt(high["value"])},
                            {"agent": market["agent"], "value": _fmt(market["value"])}],
                "message": f"The LBO enters at {_fmt(high['value'])}, below the market enterprise value of "
                           f"{_fmt(market['value'])}. A take-private needs a premium, so the stated returns do not "
                           "hold for a real offer.",
            })
        elif low["value"] > market["value"] * (1 + ENTRY_PREMIUM_LIMIT):  # even the lowest case is far above market
            premium = low["value"] / market["value"] - 1
            issues.append({
                "metric": "LBO entry vs market value", "severity": "high",
                "reports": [{"agent": low["agent"], "value": _fmt(low["value"])},
                            {"agent": market["agent"], "value": _fmt(market["value"])}],
                "message": f"The LBO's lowest entry value is {_fmt(low['value'])}, {premium:.0%} above the market "
                           f"enterprise value of {_fmt(market['value'])}. That premium is far outside usual take-private "
                           "ranges, so the LBO is not a credible offer structure.",
            })
    return issues


def reconciliation_notice(issues: List[Dict[str, Any]]) -> str:
    """Text for a memo prompt or report: the disagreements, stated plainly."""
    if not issues:
        return ""
    lines = ["CROSS-AGENT RECONCILIATION ISSUES (state these in the memo; do not average them away):"]
    lines += [f"- {issue['message']}" for issue in issues]
    return "\n".join(lines)
