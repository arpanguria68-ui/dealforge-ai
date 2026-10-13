"""Editor stage: decide what a report says, so agent output does not flood it.

Agents return long, overlapping payloads. Rendering all of it buries the conclusions,
and the same revenue figure appears under three agents. The editor produces a short,
de-duplicated brief: a few key messages and at most ``MAX_POINTS`` points per agent.

An LLM editor writes the brief when available; every point it writes is checked with
the same figure-grounding test used for memos, and points carrying figures that are not
in the agents' recorded output are dropped. A deterministic editor makes the same kind
of selection without a model and is the fallback. Full agent output stays available to
the renderers as an appendix, so editing never deletes information.
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Set

from app.core.reports.docx_rendering import agent_lines
from app.core.validation.memo_grounding import check_memo_grounding

MAX_POINTS = 5
MAX_KEY_MESSAGES = 5
MAX_POINT_CHARS = 280
EDITOR_TIMEOUT_SECONDS = 60
# Synthesis agents restate other agents; they cannot be the evidence for their own figures.
_SYNTHESIS_AGENTS = frozenset({"investment_memo_agent", "report_architect", "report_compiler", "scoring_agent"})
# Groups whose lines are conclusions rather than raw data.
_CONCLUSION_GROUPS = ("finding", "summary", "recommendation", "thesis", "risk", "conclusion", "takeaway", "insight")
_METADATA_LINE = re.compile(r"^\s*(deal id|date|prepared by|classification|to|from|subject|cc)\s*[:|]", re.IGNORECASE)
MAX_CONCLUSIONS = 3  # leave room for headline figures
_RAW_JSON_LINE = re.compile(r'"\w[\w ]*"\s*:')
_FIGURE = re.compile(r"\$?-?\d[\d,]*\.?\d*\s?(?:%|x|[MBK]\b|bn|million|billion)?")

EDITOR_SYSTEM = """You are the editor of an investment-committee report. Analysts have produced long,
overlapping outputs. Write the brief a partner reads in two minutes.

Rules:
- Use ONLY facts and figures present in the analyst outputs below. Never add numbers.
- At most {max_points} points per agent and {max_messages} key messages. Fewer is better.
- Each point is one sentence, at most 40 words, leading with the conclusion and the figure.
- Do not repeat a fact that already appears under another agent.
- Skip process notes, tool names and boilerplate. Keep caveats that change a decision.
- An agent with nothing decision-relevant gets an empty list.

Reply with ONLY this JSON:
{{"key_messages": ["..."],
  "sections": [{{"agent": "<agent_type exactly as given>", "points": ["..."]}}]}}"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _figures(text: str) -> Set[str]:
    return {m.group(0).strip().casefold() for m in _FIGURE.finditer(text) if any(c.isdigit() for c in m.group(0))}


def _trim(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= MAX_POINT_CHARS else text[: MAX_POINT_CHARS - 1].rsplit(" ", 1)[0] + "..."


def _agent_label(agent: str) -> str:
    return str(agent or "Agent").replace("_", " ").title()


def deterministic_edit(agent_results: List[Dict[str, Any]], verified: Optional[Set[float]] = None,
                       evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Rule-based brief: prefer lines with figures and top-level facts, drop repeats."""
    seen_text: Set[str] = set()
    seen_figures: Set[str] = set()
    sections = []
    by_agent: Dict[str, Dict[str, Any]] = {}
    for result in agent_results or []:
        agent = str(result.get("agent_type", "agent"))
        if agent in by_agent and len(by_agent[agent]["points"]) >= MAX_POINTS:
            continue
        candidates = []
        parents: Dict[int, str] = {}
        for kind, depth, text in agent_lines(result, verified, limit=400):
            if text.startswith("..."):
                continue
            is_label = kind == "h" or (":" not in text and not _figures(text) and len(text) < 60)
            if is_label:  # a section/group name: context for the lines under it
                parents = {d: v for d, v in parents.items() if d < depth}
                parents[depth] = text
                continue
            has_figure = bool(_figures(text))
            if depth > 3 or (len(text) < 20 and not has_figure) or _RAW_JSON_LINE.search(text)                     or _METADATA_LINE.match(text):
                continue  # too short to say anything, or a fragment of unparsed JSON
            if text.count("; ") >= 3:
                continue  # a flattened table row (e.g. one filing period): data, not a finding
            context = next((parents[d] for d in sorted(parents, reverse=True) if d < depth), "")
            group = " ".join(parents.values()).casefold()
            if context and has_figure and len(text) < 80 and _norm(context) not in _norm(text):
                text = f"{context}: {text}"
            score = (2 if has_figure else 0) + (1 if depth <= 1 else 0)
            conclusion = any(word in group for word in _CONCLUSION_GROUPS)
            candidates.append((score + (3 if conclusion else 0), conclusion, text))
        ranked = sorted(candidates, key=lambda c: -c[0])
        conclusions = [c for c in ranked if c[1]]
        ordered = conclusions[:MAX_CONCLUSIONS] + [c for c in ranked if not c[1]] + conclusions[MAX_CONCLUSIONS:]
        points: List[str] = []
        for _, _, text in ordered:
            key, figures = _norm(text), _figures(text)
            if key in seen_text or (figures and figures <= seen_figures):
                continue  # already said, here or under another agent
            points.append(_trim(text))
            seen_text.add(key)
            seen_figures |= figures
            if len(points) >= MAX_POINTS:
                break
        if agent in by_agent:  # same agent ran twice (e.g. DCF and comps tasks): one section
            by_agent[agent]["points"] = (by_agent[agent]["points"] + points)[:MAX_POINTS]
        else:
            by_agent[agent] = {"agent": agent, "points": points}
            sections.append(by_agent[agent])

    messages: List[str] = []
    for finding in (evidence or {}).get("findings", []):
        text = finding.get("text") if isinstance(finding, dict) else None
        # evidence findings are sometimes bare field codes ("revenue_cagr"): not a message
        if text and " " in text.strip() and _norm(text) not in {_norm(m) for m in messages}:
            messages.append(_trim(text))
    for section in sections:  # then the lead point of each agent
        if len(messages) >= MAX_KEY_MESSAGES:
            break
        lead = next((pt for pt in section["points"] if _figures(pt)), None)
        if lead and _norm(lead) not in {_norm(m) for m in messages}:
            messages.append(f"{_agent_label(section['agent'])}: {lead}")
    return {"key_messages": messages[:MAX_KEY_MESSAGES], "sections": sections, "editor": "deterministic"}


def _editor_prompt(agent_results: List[Dict[str, Any]], verified: Optional[Set[float]]) -> str:
    blocks = []
    budget = 2600  # characters per agent keeps the whole prompt within ~12k tokens
    for result in agent_results:
        text = "\n".join(("  " * d) + ("- " if k == "b" else "") + t for k, d, t in agent_lines(result, verified, limit=120))
        blocks.append(f"### agent_type: {result.get('agent_type', 'agent')}\n{text[:budget]}")
    return "ANALYST OUTPUTS\n\n" + "\n\n".join(blocks)


def validate_edit(candidate: Any, agent_results: List[Dict[str, Any]], user_text: str = "") -> Optional[Dict[str, Any]]:
    """Keep only well-formed, grounded points; None when nothing usable remains."""
    if not isinstance(candidate, dict):
        return None
    known = {str(r.get("agent_type")) for r in agent_results}
    evidence = [r for r in agent_results if str(r.get("agent_type")) not in _SYNTHESIS_AGENTS] or agent_results
    dropped: List[str] = []

    def grounded(text: str) -> bool:
        if not _figures(text):
            return True
        report = check_memo_grounding(text, evidence, user_text)
        if report["status"] == "no_sources":
            return True
        ok = not report["ungrounded"] and not report["unsupported_sources"]
        if not ok:
            dropped.append(text)
        return ok

    def clean_list(values: Any, limit: int) -> List[str]:
        out = []
        for value in values if isinstance(values, list) else []:
            if isinstance(value, str) and value.strip() and grounded(value):
                out.append(_trim(value))
        return out[:limit]

    sections, seen_agents = [], set()
    for section in candidate.get("sections") or []:
        agent = str(section.get("agent")) if isinstance(section, dict) else ""
        if agent in known and agent not in seen_agents:
            seen_agents.add(agent)
            sections.append({"agent": agent, "points": clean_list(section.get("points"), MAX_POINTS)})
    messages = clean_list(candidate.get("key_messages"), MAX_KEY_MESSAGES)
    if not sections and not messages:
        return None
    return {"key_messages": messages, "sections": sections, "editor": "llm", "dropped_ungrounded": dropped}


def _parse_json(text: str) -> Any:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


async def edit_report(
    agent_results: List[Dict[str, Any]],
    evidence: Dict[str, Any],
    user_text: str = "",
    verified: Optional[Set[float]] = None,
    llm_call=None,
) -> Dict[str, Any]:
    """LLM editor with grounding checks; deterministic editor on any failure or gap.

    ``llm_call(prompt, system_prompt) -> str`` is injectable for tests; by default the
    gateway is called with the provider routed for the report architect.
    """
    fallback = deterministic_edit(agent_results, verified, evidence)
    if not agent_results:
        return fallback
    if llm_call is None:
        llm_call = _gateway_call
    try:
        raw = await asyncio.wait_for(
            llm_call(_editor_prompt(agent_results, verified),
                     EDITOR_SYSTEM.format(max_points=MAX_POINTS, max_messages=MAX_KEY_MESSAGES)),
            timeout=EDITOR_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # timeout, provider down: never block document generation
        return {**fallback, "editor_error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    edited = validate_edit(_parse_json(raw), agent_results, user_text)
    if not edited:
        return {**fallback, "editor_error": "editor reply was not usable JSON"}
    # An agent the editor skipped or emptied keeps the deterministic selection.
    by_agent = {s["agent"]: s for s in edited["sections"]}
    edited["sections"] = [
        by_agent[s["agent"]] if by_agent.get(s["agent"], {}).get("points") else {**s, "editor": "deterministic"}
        for s in fallback["sections"]
    ]
    if not edited["key_messages"]:
        edited["key_messages"] = fallback["key_messages"]
    return edited


async def _gateway_call(prompt: str, system_prompt: str) -> str:
    from app.core.llm.llm_gateway import get_llm_gateway
    from app.core.llm.model_router import get_model_router

    provider, _ = await get_model_router().get_provider_for_text("report_architect", prompt[:1600])
    response = await get_llm_gateway().call(
        provider=provider, prompt=prompt, system_prompt=system_prompt, temperature=0.0, json_mode=True,
    )
    if response.get("error"):
        raise RuntimeError(str(response.get("error")))
    return response.get("content") or ""


def section_for(editorial: Optional[Dict[str, Any]], agent_type: str) -> Optional[List[str]]:
    """Edited points for an agent, or None when no editorial pass covered it."""
    for section in (editorial or {}).get("sections", []):
        if section.get("agent") == agent_type:
            return section.get("points") or []
    return None


__all__ = ["deterministic_edit", "edit_report", "validate_edit", "section_for", "MAX_POINTS"]
