"""Budgeted, valid-JSON rendering of agent context for prompts.

Agents used ``json.dumps(context)[:2000]``. That cut the JSON mid-structure
and kept whatever keys happened to come first: with a realistic orchestrator
context, the brief, Laya annotations, an object repr and the knowledge-graph
block filled the window, and ``fact_base`` / ``financial_data`` (the numbers
the agent was meant to analyse) were silently dropped.

``render_context`` instead:
- drops runtime objects and keys already delivered elsewhere (system prompt,
  retrieval blocks);
- emits the deal facts first (priority keys), then everything else;
- truncates individual long values with an explicit marker, so the JSON
  stays valid;
- lists any keys it had to omit, so the model knows data is missing rather
  than absent.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

# Never serialized into prompts: runtime objects, routing/telemetry, and blocks
# injected into the system prompt separately (skill/sector/knowledge graph).
EXCLUDED_KEYS = frozenset({
    "kb_graph", "knowledge_graph_context", "skill_context", "sector_prompt",
    "token_budget", "source_registry", "routed_provider", "local_only",
    "laya_guard", "laya_triage", "laya_guard_action", "action", "agent_results",
    "issue_tree", "branch_contexts",
})

# Deal facts first, so a tight budget never drops them in favour of metadata.
PRIORITY_KEYS = (
    "target_company", "company_name", "deal_name", "industry", "sector", "deal_goal",
    "deal_stage", "fact_base", "financial_data", "metrics", "financials", "valuation",
    "cash_data", "market_data", "reviewer_feedback", "deal_brief",
)

PER_VALUE_CHARS = 2500


def _shrink(value: Any, limit: int) -> Any:
    """Serialize-able value no longer than ~limit chars, keeping JSON valid."""
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return value
    if isinstance(value, str):
        return value[: max(0, limit - 40)] + f"…[truncated {len(value) - limit + 40} chars]"
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        used = 2
        for k, v in value.items():
            piece = _shrink(v, max(80, (limit - used) // 2))
            size = len(json.dumps({k: piece}, default=str))
            if used + size > limit:
                out["_truncated_keys"] = [key for key in value if key not in out][:20]
                break
            out[k] = piece
            used += size
        return out
    if isinstance(value, (list, tuple)):
        out_list = []
        used = 2
        for item in value:
            piece = _shrink(item, max(80, (limit - used) // 2))
            size = len(json.dumps(piece, default=str)) + 1
            if used + size > limit:
                out_list.append(f"…[{len(value) - len(out_list)} more items truncated]")
                break
            out_list.append(piece)
            used += size
        return out_list
    return str(value)[:limit]


def render_context(
    context: Optional[Dict[str, Any]],
    budget: int = 6000,
    *,
    exclude: Iterable[str] = (),
    priority: Iterable[str] = PRIORITY_KEYS,
) -> str:
    """Return valid JSON for ``context`` within ~``budget`` characters."""
    if not context:
        return "{}"
    skip = EXCLUDED_KEYS | set(exclude)
    items = {k: v for k, v in context.items() if k not in skip and not callable(v)}
    ordered = [k for k in priority if k in items] + [k for k in items if k not in set(priority)]

    rendered: Dict[str, Any] = {}
    omitted = []
    used = 2
    for key in ordered:
        piece = _shrink(items[key], min(PER_VALUE_CHARS, max(200, budget - used)))
        size = len(json.dumps({key: piece}, default=str))
        if used + size > budget:
            omitted.append(key)
            continue
        rendered[key] = piece
        used += size
    if omitted:
        rendered["_omitted_for_length"] = omitted
    return json.dumps(rendered, default=str)
