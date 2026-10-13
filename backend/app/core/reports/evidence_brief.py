"""Deterministic, evidence-only deal brief for dashboard and report use."""

from typing import Any, Dict, Iterable, List
import re


_FINDING_FIELDS = (
    "key_findings",
    "findings",
    "top_risks",
    "financial_risks",
    "key_legal_risks",
    "growth_opportunities",
    "material_issues",
)
_GAP_FIELDS = ("data_gaps", "missing_data", "unknowns", "limitations", "data_limitations", "needs_data")
_TEXT_FIELDS = ("summary", "executive_summary", "conclusion", "recommendation")
_FINANCIAL_FIELDS = (
    "revenue", "revenue_yoy_percent", "gross_profit", "gross_margin_percent",
    "operating_income", "operating_margin_percent", "net_income", "ebitda",
)
_DERIVED_FINANCIAL_FIELDS = {
    "revenue_yoy_percent",
    "gross_margin_percent",
    "operating_margin_percent",
}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, dict):
        candidates = [value.get(k) for k in ("finding", "title", "risk", "issue", "description", "text", "summary")
                      if isinstance(value.get(k), str) and value.get(k).strip()]
        # "finding": "revenue_cagr" is an identifier; the sentence beside it is the finding.
        sentences = [c for c in candidates if not re.fullmatch(r"[a-z0-9_]+", c.strip())]
        text = sentences[0] if sentences else (candidates[0] if candidates else None)
        if text:
            detail = value.get("evidence") or value.get("rationale")
            yield f"{text.strip()}: {str(detail).strip()}" if detail else str(text).strip()
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


# Notes about the narrative step ("LLM unavailable") sit beside intact tool-sourced data;
# they must not make the whole analysis, and its SEC-linked figures, disappear.
_ADVISORY_KEYS = frozenset({"provider_warning", "warnings", "warning", "data_limitations", "limitations"})


def _contains_execution_error(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("error"):
            return True
        return any(
            _contains_execution_error(item) for key, item in value.items() if key not in _ADVISORY_KEYS
        )
    if isinstance(value, list):
        return any(_contains_execution_error(item) for item in value)
    if isinstance(value, str):
        text = value.strip().lower()
        return text.startswith(("[error]", "[rate limited]", "[provider unavailable]")) or (
            "no configured provider returned a usable response" in text
        )
    return False


_CURATED_FIELDS = _FINANCIAL_FIELDS + ("cost_of_revenue", "operating_expenses", "interest_expense")


def _add_curated_financials(
    data: Dict[str, Any], agent: str, data_points: List[Dict[str, Any]],
    source_records: List[Dict[str, Any]], source_ids: Dict[str, str],
) -> None:
    """Year-keyed statements from the data curator: {"financials": {"2025": {"revenue": 457.55}}}.

    Values are scaled by the record's declared unit. They are linked to a source only when
    the curator's citation names an SEC filer (CIK), which gives a verifiable filing index.
    """
    curated = data.get("curated_deal_metrics")
    statements = curated.get("financials") if isinstance(curated, dict) else None
    if not isinstance(statements, dict):
        return
    unit = str(curated.get("unit") or "").lower()
    scale = 1e9 if "billion" in unit else 1e6 if "million" in unit else 1e3 if "thousand" in unit else 1.0

    source_id, source_url = None, None
    citations = data.get("source_citations")
    for description in (citations.values() if isinstance(citations, dict) else []):
        cik = re.search(r"CIK:?\s*(\d{6,10})", str(description))
        if cik:
            source_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik.group(1)}&type=10-K"
            if source_url not in source_ids:
                source_ids[source_url] = f"S{len(source_ids) + 1}"
                source_records.append({"id": source_ids[source_url], "url": source_url, "title": str(description)[:200]})
            source_id = source_ids[source_url]
            break

    for period, row in statements.items():
        if not isinstance(row, dict):
            continue
        for field in _CURATED_FIELDS:
            value = row.get(field)
            if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            is_ratio = field in _DERIVED_FINANCIAL_FIELDS
            data_points.append({
                "metric": field, "value": value if is_ratio else round(value * scale, 2), "period": str(period),
                "period_end": None, "agent": agent, "source_id": source_id, "source_url": source_url,
                "basis": ("derived_source_linked" if is_ratio else "reported_source_linked") if source_id
                else ("derived_unverified_no_source" if is_ratio else "unverified_no_source"),
            })


def build_evidence_brief(deal: Dict[str, Any], analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate explicit agent output; never infer a recommendation or fill missing facts."""
    findings, gaps = [], []
    successful = [
        item for item in analyses
        if item.get("success", isinstance(item.get("data"), dict))
        and isinstance(item.get("data"), dict)
        and not _contains_execution_error(item.get("data"))
    ]
    agents_with_sources = 0
    data_points = []
    source_records = []
    source_ids = {}

    for analysis in successful:
        agent = str(analysis.get("agent_type") or "unknown")
        data = analysis["data"]
        item_sources = data.get("sources") or data.get("citations") or data.get("references")
        if item_sources:
            agents_with_sources += 1
            for source in item_sources if isinstance(item_sources, list) else []:
                if not isinstance(source, dict) or not source.get("url"):
                    continue
                key = str(source["url"])
                if key not in source_ids:
                    source_ids[key] = f"S{len(source_ids) + 1}"
                    source_records.append({"id": source_ids[key], **source})
        _add_curated_financials(data, agent, data_points, source_records, source_ids)
        if any(point.get("agent") == agent and point.get("source_id") for point in data_points):
            agents_with_sources += 1
        historical = data.get("historical_financials")
        for period in historical if isinstance(historical, list) else []:
            if not isinstance(period, dict):
                continue
            source_url = period.get("source_url")
            for field in _FINANCIAL_FIELDS:
                value = period.get(field)
                if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                data_points.append({
                    "metric": field,
                    "value": value,
                    "period": period.get("fiscal_year") or period.get("period"),
                    "period_end": period.get("period_end_date"),
                    "agent": agent,
                    "source_id": source_ids.get(str(source_url)) if source_url else None,
                    "source_url": source_url,
                    "basis": (
                        "user_supplied_unverified"
                        if period.get("source_type") == "user_supplied_unverified"
                        else (
                            "derived_source_linked" if field in _DERIVED_FINANCIAL_FIELDS else "reported_source_linked"
                        ) if source_ids.get(str(source_url))
                        else (
                            "derived_unverified_no_source" if field in _DERIVED_FINANCIAL_FIELDS else "unverified_no_source"
                        )
                    ),
                })
        for section_name, definitions in (
            ("cash_flow", (
                ("operating_cash_flow", "fiscal_year", "operating_cash_flow_source_url"),
                ("capital_expenditures", "capital_expenditures_fiscal_year", "capital_expenditures_source_url"),
                ("free_cash_flow", "free_cash_flow_fiscal_year", None),
            )),
            ("balance_sheet", (
                ("cash", "cash_fiscal_year", "cash_source_url"),
                ("long_term_debt", "debt_fiscal_year", "debt_source_url"),
            )),
        ):
            section = data.get(section_name)
            if not isinstance(section, dict):
                continue
            for metric, period_field, url_field in definitions:
                value = section.get(metric)
                if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                source_url = section.get(url_field) if url_field else None
                urls = section.get("free_cash_flow_source_urls", []) if metric == "free_cash_flow" else []
                selected_urls = [source_url] if source_url else list(urls)
                point_source_ids = list(dict.fromkeys(
                    source_ids[str(url)] for url in selected_urls if str(url) in source_ids
                ))
                data_points.append({
                    "metric": metric, "value": value,
                    "period": section.get(period_field) or "Not established",
                    "agent": agent, "source_id": point_source_ids[0] if point_source_ids else None,
                    "source_ids": point_source_ids, "source_url": selected_urls[0] if selected_urls else None,
                    "basis": (
                        "derived_source_linked" if point_source_ids else "derived_unverified_no_source"
                    ) if metric == "free_cash_flow" else (
                        "reported_source_linked" if point_source_ids else "unverified_no_source"
                    ),
                })
        for field in _FINDING_FIELDS:
            for text in _strings(data.get(field)):
                findings.append({
                    "text": text[:360],
                    "agent": agent,
                    "severity": "high" if "high" in text.lower() or "critical" in text.lower() else "unrated",
                    "sources": item_sources if isinstance(item_sources, list) else [],
                })
        sections = [data]
        sections.extend(value for value in data.values() if isinstance(value, dict))
        for section in sections:
            for field in _GAP_FIELDS:
                for text in _strings(section.get(field)):
                    gaps.append({"text": text[:240], "agent": agent})
        for field in _TEXT_FIELDS:
            for text in _strings(data.get(field)):
                findings.append({"text": text[:360], "agent": agent, "severity": "unrated", "sources": item_sources if isinstance(item_sources, list) else []})

    # De-duplicate without paraphrasing or merging claims from different agents.
    unique = []
    seen = set()
    for item in findings:
        key = (item["agent"], item["text"].casefold())
        if key not in seen:
            seen.add(key)
            item["source_ids"] = list(dict.fromkeys(
                source_ids[str(source.get("url"))]
                for source in item.get("sources", [])
                if isinstance(source, dict) and str(source.get("url")) in source_ids
            ))
            unique.append(item)
    for point in data_points:
        metric = str(point.get("metric") or "Financial metric").replace("_", " ").title()
        issues = []
        if not point.get("source_id"):
            issues.append("no linked source citation")
        period = str(point.get("period") or "").strip().casefold()
        if period in {"", "not established", "unknown", "n/a"}:
            issues.append("fiscal period not established")
        if issues:
            gaps.append({
                "text": f"{metric}: " + " and ".join(issues) + ".",
                "agent": point.get("agent", "unknown"),
            })

    gap_unique, seen_gaps = [], set()
    for item in gaps:
        key = item["text"].casefold()
        if key not in seen_gaps:
            seen_gaps.add(key)
            gap_unique.append(item)

    recommendation = deal.get("final_recommendation")
    if isinstance(recommendation, str) and re.match(
        r"^\s*\d+\s*/\s*\d+\s+tasks?\s+completed(?:\b|;)", recommendation, re.IGNORECASE
    ):
        recommendation = None
    return {
        "deal_id": deal.get("id"),
        "status": deal.get("status", "unknown"),
        "recommendation": recommendation,
        "score": deal.get("final_score"),
        "score_recorded": deal.get("final_score") is not None,
        "analysis_count": len(analyses),
        "successful_analysis_count": len(successful),
        "agents_with_citations": agents_with_sources,
        "data_points": data_points[:120],
        "sources": source_records[:80],
        "findings": unique[:12],
        "unknowns": gap_unique[:12],
        "notice": "Compiled from recorded agent outputs. Unreported values remain unknown; findings are not independently verified.",
    }
