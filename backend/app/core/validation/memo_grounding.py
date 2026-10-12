"""Deterministic check that a memo's figures trace back to agent outputs.

An LLM judge cannot tell a sourced number from a fluent invented one, so this
compares the figures written in a memo ($ amounts, percentages, multiples)
with the numbers the agents actually produced, and flags data vendors the memo
cites that appear nowhere in the evidence. Figures the memo derives itself
(scenario IRRs, implied multiples) are legitimately absent from the sources and are
listed too: the result is a ratio and a list for review, not a pass/fail per number.
A generic "is it a ratio of two evidence numbers" rule was tried and dropped: with
dozens of numbers it excuses almost any invented figure.
"""

import json
import re
from typing import Any, Dict, Iterator, List, Set

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
_MONEY = re.compile(
    r"\$\s?(-?\d[\d,]*(?:\.\d+)?)\s?(billion|bn|million|mm|[BMK])?(?![A-Za-z])", re.IGNORECASE
)
_PERCENT = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s?%")
_MULTIPLE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?x\b", re.IGNORECASE)
_UNIT_TO_MILLIONS = {"b": 1000.0, "bn": 1000.0, "billion": 1000.0,
                     "m": 1.0, "mm": 1.0, "million": 1.0, "k": 0.001}
# Vendors/publishers an LLM tends to cite without having used them.
_VENDORS = ("bloomberg", "capital iq", "factset", "gartner", "idc", "pitchbook",
            "refinitiv", "morningstar", "forrester")
LOW_GROUNDING_RATIO = 0.7  # the memo's own implied ratios are not in the evidence, so not 1.0


def _leaves(node: Any) -> Iterator[Any]:
    if isinstance(node, dict):
        for value in node.values():
            yield from _leaves(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _leaves(item)
    else:
        yield node


def _source_numbers(agent_results: List[Dict[str, Any]]) -> tuple:
    """Evidence numbers: (as written, rescaled to thousands/millions/billions/percent)."""
    base: Set[float] = set()
    for result in agent_results or []:
        for leaf in _leaves(result.get("data") if isinstance(result, dict) else result):
            if isinstance(leaf, bool):
                continue
            if isinstance(leaf, (int, float)):
                base.add(float(leaf))
            elif isinstance(leaf, str):
                for match in _NUMBER.findall(leaf):
                    try:
                        base.add(float(match.replace(",", "")))
                    except ValueError:
                        pass
    variants: Set[float] = set()
    for value in base:
        for scale in (1.0, 1e3, 1e6, 1e9):
            variants.add(value / scale)
        variants.add(value * 100)  # 0.1272 stored, "12.72%" written
    return base, variants


def _close(figure: float, candidate: float) -> bool:
    return abs(figure - candidate) <= max(0.011, abs(figure) * 0.005)  # rounding only


def _matches(figure: float, sources: Set[float]) -> bool:
    return any(_close(figure, candidate) for candidate in sources)


def _memo_figures(memo: str) -> List[Dict[str, Any]]:
    figures: List[Dict[str, Any]] = []
    seen: Set[tuple] = set()

    def add(kind: str, value: float, text: str) -> None:
        key = (kind, round(value, 4))
        if key not in seen:
            seen.add(key)
            figures.append({"kind": kind, "value": value, "text": text.strip()})

    for m in _MONEY.finditer(memo):
        value = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        add("money", value * _UNIT_TO_MILLIONS[unit] if unit else value, m.group(0))
    for m in _PERCENT.finditer(memo):
        add("percent", float(m.group(1)), m.group(0))
    for m in _MULTIPLE.finditer(memo):
        add("multiple", float(m.group(1)), m.group(0))
    return figures


def check_memo_grounding(
    memo: str, agent_results: List[Dict[str, Any]], user_text: str = ""
) -> Dict[str, Any]:
    """Return how many memo figures are found in (or derive from) the agents' outputs.

    ``user_text`` is the request itself: targets the user stated (a 20% IRR, 6x
    leverage) are inputs, not claims the model made up.
    """
    _, sources = _source_numbers(agent_results)
    if user_text:
        sources |= {float(m.replace(",", "")) for m in _NUMBER.findall(user_text) if m.strip("-,.")}
    evidence_text = json.dumps(
        [r.get("data") if isinstance(r, dict) else r for r in agent_results or []],
        default=str,
    ).lower()
    unsupported = sorted({v for v in _VENDORS if v in (memo or "").lower() and v not in evidence_text})
    if not sources:
        return {"status": "no_sources", "figures_total": 0, "figures_grounded": 0,
                "grounded_ratio": 0.0, "ungrounded": [], "unsupported_sources": unsupported}

    figures = _memo_figures(memo or "")
    ungrounded = [
        f["text"] for f in figures
        if not _matches(f["value"], sources)
    ]
    total = len(figures)
    grounded = total - len(ungrounded)
    ratio = round(grounded / total, 3) if total else 1.0
    status = "ok" if ratio >= LOW_GROUNDING_RATIO and not unsupported else "low_grounding"
    return {"status": status, "figures_total": total, "figures_grounded": grounded,
            "grounded_ratio": ratio, "ungrounded": ungrounded[:30],
            "unsupported_sources": unsupported}


def grounding_notice(report: Dict[str, Any]) -> str:
    """Human-readable provenance warning appended to a memo that fails the check."""
    lines = ["", "---", "", "## Data provenance warning",
             f"{report['figures_grounded']} of {report['figures_total']} figures in this memo "
             "match the agents' recorded outputs; the rest were not found in the evidence "
             "(they may be figures the memo computed itself, or unverified) and need review before use."]
    if report.get("ungrounded"):
        lines.append("Figures not found in the evidence: " + ", ".join(report["ungrounded"][:15]) + ".")
    if report.get("unsupported_sources"):
        lines.append("Cited sources with no matching tool output: "
                     + ", ".join(report["unsupported_sources"]) + ".")
    return "\n".join(lines) + "\n"
