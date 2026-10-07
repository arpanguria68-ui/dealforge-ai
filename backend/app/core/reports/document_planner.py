"""Evidence-bounded planning and curation for generated deliverables."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List

from app.core.reports.evidence_brief import build_evidence_brief


ALLOWED_SECTIONS = {
    "executive_summary": "Executive Summary",
    "financial_metrics": "Financial Metrics",
    "market_analysis": "Market Analysis",
    "risk_assessment": "Risk Assessment",
    "valuation": "Valuation",
    "agent_findings": "Agent Findings",
    "diligence_gaps": "Diligence Gaps",
    "sources_methodology": "Sources and Methodology",
    "next_steps": "Next Steps",
}

_SECTION_ALIASES = {label.casefold(): key for key, label in ALLOWED_SECTIONS.items()}
_SECTION_ALIASES.update({key.casefold(): key for key in ALLOWED_SECTIONS})
_SECTION_ALIASES.update({key.replace("_", " "): key for key in ALLOWED_SECTIONS})
ALLOWED_VISUALS = {"deal_score", "historical_revenue", "valuation_range"}
SYNTHESIS_TIMEOUT_SECONDS = 60


def extract_chat_context(conversations: Iterable[Dict[str, Any]], deal_id: str) -> Dict[str, str]:
    """Return the latest user mandate and final chat summary linked to this deal."""
    matching = [item for item in conversations if isinstance(item, dict) and item.get("dealId") == deal_id]
    matching.sort(key=lambda item: item.get("updatedAt", 0))
    user_prompt = ""
    summary = ""
    for conversation in matching:
        for message in conversation.get("messages", []) if isinstance(conversation.get("messages"), list) else []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if message.get("role") == "user":
                user_prompt = content.strip()[-6000:]
            metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            if metadata.get("_isSynthesis") or message.get("agentName") == "DealForge Summary":
                summary = content.strip()[-10000:]
    return {key: value for key, value in (("mandate", user_prompt), ("chat_summary", summary)) if value}


def latest_agent_outputs(activities: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the newest successful result per agent; fall back to its newest failure."""
    selected: Dict[str, Dict[str, Any]] = {}
    for item in activities:
        agent = str(item.get("agent_type") or "unknown")
        current = selected.get(agent)
        successful = bool(item.get("success", isinstance(item.get("data"), dict)))
        current_successful = bool(current and current.get("success", isinstance(current.get("data"), dict)))
        if current is None or (successful and not current_successful) or successful == current_successful:
            selected[agent] = item
    return list(selected.values())


def build_report_agent_results(
    task_lists: Iterable[Any], activities: Iterable[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Use persisted completed tasks as report inputs; activity data only enriches metadata."""
    task_lists = list(task_lists)
    activity_rows = [item for item in activities if isinstance(item, dict)]
    if not task_lists:
        return activity_rows

    consumed = set()
    results = []
    for task_list in task_lists:
        items = getattr(task_list, "items", None)
        if items is None and isinstance(task_list, dict):
            items = task_list.get("items", [])
        for task in items or []:
            status = task.get("status") if isinstance(task, dict) else getattr(task, "status", None)
            data = task.get("result") if isinstance(task, dict) else getattr(task, "result", None)
            if status != "done" or not isinstance(data, dict):
                continue
            task_id = task.get("id") if isinstance(task, dict) else getattr(task, "id", None)
            agent = task.get("assigned_agent") if isinstance(task, dict) else getattr(task, "assigned_agent", None)
            title = task.get("title") if isinstance(task, dict) else getattr(task, "title", None)
            timestamp = task.get("updated_at") if isinstance(task, dict) else getattr(task, "updated_at", None)
            match_index = next((
                index for index in range(len(activity_rows) - 1, -1, -1)
                if index not in consumed and task_id and activity_rows[index].get("task_id") == task_id
            ), None)
            if match_index is None:
                match_index = next((
                    index for index in range(len(activity_rows) - 1, -1, -1)
                    if index not in consumed
                    and activity_rows[index].get("agent_type") == agent
                    and activity_rows[index].get("data") == data
                ), None)
            metadata = {}
            if match_index is not None:
                consumed.add(match_index)
                metadata = {
                    key: value for key, value in activity_rows[match_index].items()
                    if key not in {"data", "success", "agent_type", "task_id"}
                }
            results.append({
                **metadata,
                "agent_type": agent or "unknown",
                "task_id": task_id,
                "task_title": title,
                "success": True,
                "status": "done",
                "data": data,
                "timestamp": timestamp or metadata.get("timestamp"),
            })
    return results


def default_document_blueprint(deal: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Create a safe plan without an LLM, based solely on explicit data coverage."""
    agent_names = {str(item.get("agent_type", "")) for item in evidence.get("source_agents", [])}
    has_metrics = bool(evidence.get("available_data", {}).get("financial_metrics"))
    sections = ["executive_summary"]
    if has_metrics:
        sections.append("financial_metrics")
    if "market_researcher" in agent_names:
        sections.append("market_analysis")
    if "risk_assessor" in agent_names:
        sections.append("risk_assessment")
    if "valuation_agent" in agent_names or "dcf_lbo_architect" in agent_names:
        sections.append("valuation")
    sections.extend(["agent_findings", "diligence_gaps", "sources_methodology"])
    if evidence.get("unknowns"):
        sections.append("next_steps")
    return {
        "report_type": "Due Diligence Report",
        "audience": str(deal.get("report_audience") or "Investment Committee"),
        "purpose": "Summarize recorded analysis, evidence coverage, and unresolved diligence questions.",
        "sections": sections,
        "visuals": [
            visual for visual, available in (
                ("deal_score", deal.get("final_score") is not None),
                ("historical_revenue", bool((deal.get("fact_base") or {}).get("metrics", {}).get("historical_revenue"))),
                ("valuation_range", bool(deal.get("valuation_output") or deal.get("financial_output"))),
            ) if available
        ],
        "planning_rationale": "Sections and visuals are limited to recorded analysis and available source data.",
        "omitted_sections": [section for section in ALLOWED_SECTIONS if section not in sections],
        "density": "standard",
        "page_orientation": "portrait",
    }


def validate_document_blueprint(candidate: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Allowlist model decisions and preserve the deterministic fallback on bad output."""
    if not isinstance(candidate, dict):
        return fallback
    sections = []
    for section in candidate.get("sections", []):
        key = _SECTION_ALIASES.get(str(section).strip().casefold())
        if key and key in fallback["sections"] and key not in sections:
            sections.append(key)
    if not sections:
        sections = list(fallback["sections"])
    elif "executive_summary" in fallback["sections"] and "executive_summary" not in sections:
        sections.insert(0, "executive_summary")
    visuals = [
        visual for visual in candidate.get("visuals", [])
        if isinstance(visual, str) and visual in ALLOWED_VISUALS and visual in fallback["visuals"]
    ] if isinstance(candidate.get("visuals", []), list) else []
    omitted = [
        key for item in candidate.get("omitted_sections", [])
        if (key := _SECTION_ALIASES.get(str(item).strip().casefold())) and key in fallback["omitted_sections"]
    ][:8] if isinstance(candidate.get("omitted_sections", []), list) else []
    return {
        **fallback,
        "report_type": "Due Diligence Report",
        "audience": fallback["audience"],
        "purpose": fallback["purpose"],
        "sections": sections,
        "visuals": visuals,
        "planning_rationale": str(candidate.get("planning_rationale") or fallback["planning_rationale"])[:500],
        "omitted_sections": omitted,
        "density": candidate.get("density") if candidate.get("density") in {"compact", "standard", "detailed"} else fallback["density"],
        "page_orientation": candidate.get("page_orientation") if candidate.get("page_orientation") in {"portrait", "landscape"} else fallback["page_orientation"],
    }


def _metric_conflicts(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    observed: Dict[str, List[Dict[str, str]]] = {}
    metric_terms = ("revenue", "arr", "mrr", "ebitda", "margin", "growth", "cash", "burn", "debt", "valuation", "wacc", "nrr", "churn")
    for result in results:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        agent = str(result.get("agent_type") or "unknown")
        stack = [(data, "")]
        while stack:
            current, prefix = stack.pop()
            for name, value in current.items():
                path = f"{prefix}.{name}" if prefix else str(name)
                if isinstance(value, dict):
                    stack.append((value, path))
                elif isinstance(value, (str, int, float)) and not isinstance(value, bool) and any(term in str(name).casefold() for term in metric_terms):
                    observed.setdefault(path.casefold(), []).append({"agent": agent, "value": str(value)[:120]})
    conflicts = []
    for name, values in observed.items():
        distinct = {item["value"].strip().casefold() for item in values}
        agents = {item["agent"] for item in values}
        if len(distinct) > 1 and len(agents) > 1:
            conflicts.append({"metric": name, "reports": values[:6]})
    return conflicts[:12]


def _deterministic_synthesis(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Readable no-model report narrative built only from curated recorded evidence."""
    points = evidence.get("data_points", [])
    financial = [point for point in points if point.get("agent") == "financial_analyst"]
    periods = list(dict.fromkeys(
        str(point.get("period")) for point in financial
        if point.get("period") and str(point.get("period")).casefold() not in {"not established", "unknown", "n/a"}
    ))
    if periods:
        coverage = f"Financial data points are recorded for {', '.join(periods)}."
    elif financial:
        coverage = "Financial data points are recorded, but the fiscal period is not established."
    else:
        coverage = "No structured financial data points were established in the saved analysis."
    unknowns = evidence.get("unknowns", [])
    complication = (
        "Open diligence items remain: " + "; ".join(item.get("text", "") for item in unknowns[:2])
        if unknowns else "The saved record does not establish material open diligence items."
    )
    findings = evidence.get("findings", [])
    key_takeaways = []
    revenue_points = sorted(
        (point for point in financial if point.get("metric") == "revenue" and isinstance(point.get("value"), (int, float))),
        key=lambda point: int(re.search(r"\d{4}", str(point.get("period", ""))).group())
        if re.search(r"\d{4}", str(point.get("period", ""))) else 0,
    )
    if len(revenue_points) >= 2:
        first, last = revenue_points[0], revenue_points[-1]
        first_year = int(re.search(r"\d{4}", str(first.get("period"))).group())
        last_year = int(re.search(r"\d{4}", str(last.get("period"))).group())
        if last_year > first_year and first["value"] > 0:
            cagr = ((last["value"] / first["value"]) ** (1 / (last_year - first_year)) - 1) * 100
            cited = list(dict.fromkeys(point.get("source_id") for point in (first, last) if point.get("source_id")))
            citations = " ".join(f"[{source_id}]" for source_id in cited)
            key_takeaways.append({
                "title": "Revenue trend",
                "description": f"Reported revenue increased from {first['value']:,.0f} in {first['period']} to {last['value']:,.0f} in {last['period']}; the derived CAGR was {cagr:.2f}% ({citations}). This is historical growth, not a forecast.",
            })
    if len(revenue_points) >= 3:
        growth_points = [point for point in financial if point.get("metric") == "revenue_yoy_percent" and isinstance(point.get("value"), (int, float))]
        growth_points.sort(key=lambda point: int(re.search(r"\d{4}", str(point.get("period", ""))).group()) if re.search(r"\d{4}", str(point.get("period", ""))) else 0)
        if len(growth_points) >= 2:
            prior, current = growth_points[-2:]
            delta = current["value"] - prior["value"]
            direction = "accelerated" if delta > 0 else "decelerated" if delta < 0 else "was unchanged"
            cited = list(dict.fromkeys(point.get("source_id") for point in (prior, current) if point.get("source_id")))
            citations = " ".join(f"[{source_id}]" for source_id in cited)
            key_takeaways.append({
                "title": "Growth trajectory",
                "description": f"Revenue growth {direction} by {abs(delta):.2f} percentage points, from {prior['value']:.2f}% in {prior['period']} to {current['value']:.2f}% in {current['period']} ({citations}).",
            })
    margin_points = [
        point for point in financial
        if point.get("metric") == "operating_margin_percent" and isinstance(point.get("value"), (int, float))
    ]
    margin_points.sort(key=lambda point: int(re.search(r"\d{4}", str(point.get("period", ""))).group()) if re.search(r"\d{4}", str(point.get("period", ""))) else 0)
    if len(margin_points) >= 2:
        first, last = margin_points[0], margin_points[-1]
        delta = last["value"] - first["value"]
        cited = list(dict.fromkeys(point.get("source_id") for point in (first, last) if point.get("source_id")))
        citations = " ".join(f"[{source_id}]" for source_id in cited)
        key_takeaways.append({
            "title": "Operating margin",
            "description": f"Reported operating margin changed by {delta:+.2f} percentage points, from {first['value']:.2f}% in {first['period']} to {last['value']:.2f}% in {last['period']} ({citations}). This is a historical comparison, not a causal explanation.",
        })
    if not key_takeaways:
        key_takeaways = [
            {
                "title": item.get("agent", "Agent").replace("_", " ").title(),
                "description": item.get("text", "") + (
                    f" [Source: {item.get('source_ids')[0]}]" if item.get("source_ids") else ""
                ),
            }
            for item in findings[:4]
        ]
    record_recommendation = evidence.get("recommendation")
    financial_narrative = coverage + " Values are reproduced from saved analysis records; no independent verification or forecast is implied."
    if key_takeaways and key_takeaways[0].get("title") == "Revenue trend":
        financial_narrative = key_takeaways[0]["description"]
        for takeaway in key_takeaways[1:3]:
            financial_narrative += " " + takeaway["description"]
        financial_narrative += " These historical comparisons do not establish future performance."
    key_metrics = {}
    for point in financial[:16]:
        label = f"{str(point.get('metric', 'Metric')).replace('_', ' ').title()} {point.get('period') or ''}".strip()
        source_id = f" [{point['source_id']}]" if point.get("source_id") else " [source not recorded]"
        key_metrics[label] = f"{point.get('value')}{source_id}"
    return {
        "executive_summary": {
            "situation": coverage,
            "complication": complication,
            "question": "The decision question must be confirmed against the deal mandate and intended use of this analysis.",
            "answer": record_recommendation or "No recommendation recorded; human review required.",
        },
        "key_takeaways": key_takeaways[:4],
        "financial_synthesis": {
            "narrative": financial_narrative,
            "key_metrics": key_metrics,
        },
        "risk_matrix": [],
        "action_items": [item.get("text", "") for item in unknowns[:6]],
    }


def _curate_synthesis(candidate: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Retain model prose only within the recorded source, metric, and decision boundaries."""
    fallback = _deterministic_synthesis(evidence)
    source_ids = {item.get("id") for item in evidence.get("sources", []) if item.get("id")}
    allowed_agents = {item.get("agent") for item in evidence.get("findings", [])}

    def numeric_tokens(text: str) -> set[str]:
        tokens = set()
        for token in re.findall(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?", text):
            try:
                tokens.add(str(Decimal(token.replace(",", "").rstrip("%" )).normalize()))
            except InvalidOperation:
                continue
        return tokens

    fallback_texts = [fallback["financial_synthesis"]["narrative"]]
    fallback_texts.extend(item["description"] for item in fallback["key_takeaways"])
    supported_numbers = set().union(*(numeric_tokens(text) for text in fallback_texts))
    for point in evidence.get("data_points", []):
        value = point.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            for representation in (str(value), f"{value:,.2f}", f"{value:,.0f}"):
                supported_numbers.update(numeric_tokens(representation))
            if abs(value) >= 1_000_000_000:
                supported_numbers.update(numeric_tokens(f"{value / 1_000_000_000:.1f}"))
            elif abs(value) >= 1_000_000:
                supported_numbers.update(numeric_tokens(f"{value / 1_000_000:.1f}"))
        year = re.search(r"\d{4}", str(point.get("period", "")))
        if year:
            supported_numbers.add(str(Decimal(year.group()).normalize()))
    for source in evidence.get("sources", []):
        supported_numbers.update(numeric_tokens(str(source.get("filed") or "")))
        supported_numbers.update(numeric_tokens(str(source.get("form") or "")))

    def is_numerically_grounded(text: str) -> bool:
        return numeric_tokens(text).issubset(supported_numbers)

    curated = dict(fallback)

    summary = candidate.get("executive_summary")
    if (isinstance(summary, dict) and isinstance(summary.get("question"), str)
            and is_numerically_grounded(summary["question"])):
        curated["executive_summary"]["question"] = summary["question"][:800]
    if evidence.get("recommendation"):
        curated["executive_summary"]["answer"] = fallback["executive_summary"]["answer"]

    takeaways = []
    for item in candidate.get("key_takeaways", []) if isinstance(candidate.get("key_takeaways"), list) else []:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "")[:1000]
        cited = set(re.findall(r"\[(S\d+)\]", description))
        source_agent = next((agent for agent in allowed_agents if agent and agent.replace("_", " ").casefold() in description.casefold()), None)
        if ((cited and cited.issubset(source_ids) or source_agent)
                and is_numerically_grounded(description)):
            takeaways.append({"title": str(item.get("title") or "Evidence-backed finding")[:160], "description": description})
        if len(takeaways) == 4:
            break
    curated["key_takeaways"] = takeaways or fallback["key_takeaways"]

    finance = candidate.get("financial_synthesis")
    if isinstance(finance, dict) and isinstance(finance.get("narrative"), str) and finance["narrative"].strip():
        candidate_narrative = finance["narrative"].strip()
        cited = set(re.findall(r"\[(S\d+)\]", candidate_narrative))
        if cited and cited.issubset(source_ids) and is_numerically_grounded(candidate_narrative):
            curated["financial_synthesis"]["narrative"] = candidate_narrative[:2400]
    # Financial values and risk judgments in the artifact always come from the curated record,
    # never from free-form model output.
    return curated


async def prepare_document_payload(registry: Any, deal: Dict[str, Any], activities: List[Dict[str, Any]]) -> tuple:
    """Run synthesis and architecture once, returning curated results and audit context."""
    agent_results = latest_agent_outputs(activities)
    evidence = build_evidence_brief(deal, agent_results)
    explicit_metrics = []
    for result in agent_results:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        for key in ("financial_metrics", "key_metrics", "metrics"):
            value = data.get(key)
            if isinstance(value, dict):
                explicit_metrics.extend(value.keys())
    risk_available = any(
        isinstance((item.get("data") or {}).get("risks"), list) and (item.get("data") or {}).get("risks")
        for item in agent_results if isinstance(item.get("data"), dict)
    )
    evidence["available_data"] = {
        "financial_metrics": bool(explicit_metrics or evidence.get("data_points")),
        "risk_register": risk_available,
        "valuation": bool(deal.get("valuation_output") or deal.get("financial_output")),
        "market_findings": any("market" in str(item.get("agent_type", "")) for item in agent_results),
    }
    evidence["source_agents"] = [
        {"agent_type": item.get("agent_type", "unknown"), "success": bool(item.get("success", True))}
        for item in agent_results
    ]
    evidence["metric_names"] = list(dict.fromkeys(
        [str(name) for name in explicit_metrics]
        + [str(point.get("metric")) for point in evidence.get("data_points", []) if point.get("metric")]
    ))[:40]
    evidence["metric_conflicts"] = _metric_conflicts(agent_results)
    fallback = default_document_blueprint(deal, evidence)
    analyst_data: Dict[str, Any] = {}
    synthesis_provider = None
    qa_warnings = []

    ba_agent = registry.get("business_analyst") if registry else None
    if ba_agent is not None:
        try:
            from app.agents.base import BaseAgent
            from app.core.llm.model_router import get_model_router

            if isinstance(ba_agent, BaseAgent):
                assigned_provider = get_model_router().get_provider_for_agent("business_analyst")
                active_client = getattr(ba_agent, "llm", None)
                client_provider = getattr(active_client, "provider", "") or (
                    active_client.__class__.__name__.lower().replace("client", "")
                    if active_client is not None else ""
                )
                if client_provider.casefold() != assigned_provider.casefold():
                    from app.agents.business_analyst import BusinessAnalystAgent

                    ba_agent = BusinessAnalystAgent()
        except Exception:
            # Preserve the registered agent if routing refresh is unavailable.
            pass
    calls = []
    if ba_agent and evidence.get("successful_analysis_count", 0):
        synthesis_inputs = {
            "data_points": evidence.get("data_points", []),
            "findings": evidence.get("findings", []),
            "unknowns": evidence.get("unknowns", []),
            "metric_conflicts": evidence.get("metric_conflicts", []),
            "sources": evidence.get("sources", []),
            "recorded_recommendation": evidence.get("recommendation"),
            "score_recorded": evidence.get("score_recorded", False),
        }
        calls.append(("synthesis", ba_agent.run(
            "Create concise evidence-grounded report synthesis",
            context={"deal_id": deal.get("id"), "deal_data": synthesis_inputs, "evidence_brief": synthesis_inputs},
        )))
    if calls:
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(call for _, call in calls), return_exceptions=True),
                timeout=SYNTHESIS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            results = [TimeoutError("Document synthesis exceeded its time budget.") for _ in calls]
        for (kind, _), result in zip(calls, results):
            if isinstance(result, Exception) or not getattr(result, "success", False):
                qa_warnings.append("Report synthesis unavailable; deterministic evidence-safe narrative used.")
            elif kind == "synthesis" and isinstance(result.data, dict):
                analyst_data = result.data
                synthesis_provider = getattr(ba_agent, "_last_llm_provider", None)
    elif not evidence.get("successful_analysis_count", 0):
        qa_warnings.append("No successful analysis outputs; LLM synthesis and architecture were skipped.")
    elif not calls:
        qa_warnings.append("Synthesis agent unavailable; deterministic narrative and layout defaults used.")

    analyst_data = _curate_synthesis(analyst_data, evidence)

    analyst_data["_evidence_brief"] = evidence
    analyst_data["_report_blueprint"] = fallback
    uncited_financial_points = any(
        not point.get("source_id") and not point.get("source_ids")
        for point in evidence.get("data_points", [])
    )
    requires_review = (
        bool(qa_warnings)
        or bool(evidence.get("unknowns"))
        or bool(evidence.get("metric_conflicts"))
        or evidence.get("successful_analysis_count", 0) == 0
        or not evidence.get("sources")
        or uncited_financial_points
    )
    if uncited_financial_points:
        qa_warnings.append("One or more financial data points have no linked source citation.")
    analyst_data["_document_qa"] = {
        "status": "review_required" if requires_review else "ready_for_review",
        "warnings": qa_warnings,
        "successful_agent_outputs": evidence.get("successful_analysis_count", 0),
        "recorded_unknowns": len(evidence.get("unknowns", [])),
        "metric_conflicts": len(evidence.get("metric_conflicts", [])),
        "synthesis_provider": synthesis_provider or "deterministic_fallback",
        "planning_source": "deterministic_evidence_coverage",
        "narrative_validation": "Model narrative is constrained to curated evidence but still requires human verification.",
        "note": "AI-generated synthesis and layout plan; verify all material statements against cited source records.",
    }
    return agent_results, analyst_data, evidence
