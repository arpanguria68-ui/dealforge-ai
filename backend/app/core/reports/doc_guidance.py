"""Minimal guidance layer for LLM-authored documents.

Content and presentation are kept apart (the usual structured-output pattern):

    guidance  ->  LLM response  ->  parse  ->  validate  ->  document model  ->  renderers

* ``guide_for`` / ``llm_instructions`` / ``response_schema`` tell a model exactly
  what each section of a document type must contain (purpose, shape, limits).
* ``parse_llm_response`` + ``validate_content`` accept whatever the model returned
  (JSON, fenced JSON, prose around JSON, lenient section shapes), enforce the
  structure and report problems instead of silently fixing or truncating them.
* ``build_model`` produces the same model ``document_workflow`` renderers use, so
  DOCX / PDF / XLSX / PPTX all work with no extra rendering code.

LLM-supplied text is never presented as verified: the model is always marked
``review_required`` and carries a provenance warning plus any validation warnings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.reports.document_workflow import DOC_TYPES, SECTION_TITLES

MAX_CONTENT_BYTES = 200_000
MAX_CELL_CHARS = 400

# What each section is for and the shape it takes. ``max_words`` / ``max_items`` are
# targets for a standard-density document; compact documents use the tighter value.
SECTION_GUIDE: Dict[str, Dict[str, Any]] = {
    "snapshot": {
        "shape": "table", "columns": ["Item", "Value"], "max_rows": 8,
        "purpose": "Key facts about the deal at a glance.",
        "guidance": "Target, industry, stage, size, recommendation. One fact per row; no commentary.",
    },
    "executive_summary": {
        "shape": "paragraphs", "max_words": 250, "compact_words": 120,
        "purpose": "The whole case in under a minute.",
        "guidance": "Lead with the recommendation or decision question, then the two or three facts that drive it. No new claims that the later sections do not support.",
    },
    "investment_thesis": {
        "shape": "bullets", "max_items": 6, "compact_items": 4,
        "purpose": "Why this deal could create value.",
        "guidance": "Each bullet is one distinct thesis point with its supporting evidence. State the assumption it depends on.",
    },
    "financial_metrics": {
        "shape": "table", "columns": ["Metric", "Period", "Value", "Source"], "max_rows": 25, "compact_rows": 8,
        "purpose": "The financial facts the case rests on.",
        "guidance": "Only figures you were given; include the period and a source id for every row. If a figure is unknown, write 'Not available' rather than estimating.",
    },
    "valuation": {
        "shape": "table", "columns": ["Measure", "Value", "Basis"], "max_rows": 12,
        "purpose": "What the business is worth and on what basis.",
        "guidance": "Report valuation outputs with their method and key assumptions. Do not present a range you were not given.",
    },
    "market_analysis": {
        "shape": "bullets", "max_items": 8, "compact_items": 5,
        "purpose": "Market size, growth, competition and positioning.",
        "guidance": "Each bullet is one market fact or competitive point with a source. Separate fact from opinion.",
    },
    "risk_assessment": {
        "shape": "table", "columns": ["Risk", "Severity", "Category", "Mitigation"], "max_rows": 20, "compact_rows": 8,
        "purpose": "What could go wrong and what can be done about it.",
        "guidance": "Rank by severity (1-10). Every risk needs a mitigation or the explicit text 'None identified'.",
    },
    "diligence_gaps": {
        "shape": "bullets", "max_items": 10, "compact_items": 6,
        "purpose": "What is still unknown.",
        "guidance": "List missing evidence and what it blocks. Be specific about the data needed.",
    },
    "next_steps": {
        "shape": "bullets", "max_items": 8, "compact_items": 5,
        "purpose": "Actions that follow from this document.",
        "guidance": "Imperative, owner-assignable actions. One action per bullet.",
    },
    "sources_methodology": {
        "shape": "bullets", "max_items": 40,
        "purpose": "Where the facts came from.",
        "guidance": "One bullet per source: '[id] title (url)'. State how the content was produced.",
    },
}

_FIGURE_RE = re.compile(r"(\$|€|£)\s?\d|\d\s?%|\b\d[\d,]*(\.\d+)?\s?(m|bn|million|billion|k)\b", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Sections whose figures should always name a source.
_NEEDS_SOURCE = {"financial_metrics", "valuation", "market_analysis", "investment_thesis"}


class ContentError(ValueError):
    """The LLM response could not be understood at all."""


# ── Guidance (what to ask the LLM for) ─────────────────────────────────

def _doc_type(doc_type: str) -> Dict[str, Any]:
    if doc_type not in DOC_TYPES:
        raise ContentError(f"Unknown doc_type '{doc_type}'. Choose one of: {', '.join(sorted(DOC_TYPES))}.")
    return DOC_TYPES[doc_type]


def section_keys(doc_type: str) -> List[str]:
    spec = _doc_type(doc_type)
    wanted = set(spec["required"]) | set(spec["optional"])
    return [k for k in SECTION_TITLES if k in wanted]


def _limits(key: str, compact: bool) -> Dict[str, int]:
    g = SECTION_GUIDE[key]
    out: Dict[str, int] = {}
    if "max_words" in g:
        out["max_words"] = g["compact_words"] if compact and "compact_words" in g else g["max_words"]
    if "max_items" in g:
        out["max_items"] = g["compact_items"] if compact and "compact_items" in g else g["max_items"]
    if "max_rows" in g:
        out["max_rows"] = g["compact_rows"] if compact and "compact_rows" in g else g["max_rows"]
    return out


def guide_for(doc_type: str, audience: Optional[str] = None) -> Dict[str, Any]:
    spec = _doc_type(doc_type)
    compact = spec.get("density") == "compact"
    sections = []
    for key in section_keys(doc_type):
        g = SECTION_GUIDE[key]
        sections.append({
            "key": key, "title": SECTION_TITLES[key], "required": key in spec["required"],
            "shape": g["shape"], "purpose": g["purpose"], "guidance": g["guidance"],
            **({"columns": g["columns"]} if g["shape"] == "table" else {}),
            **_limits(key, compact),
        })
    return {
        "doc_type": doc_type, "title": spec["title"], "audience": audience or spec["audience"],
        "formats": spec["formats"], "density": spec["density"], "sections": sections,
    }


def response_schema(doc_type: str) -> Dict[str, Any]:
    """JSON Schema for the canonical response (usable as a structured-output / tool schema)."""
    guide = guide_for(doc_type)
    table = {
        "type": "object", "required": ["columns", "rows"], "additionalProperties": False,
        "properties": {"columns": {"type": "array", "items": {"type": "string"}},
                       "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}},
    }
    section = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "paragraphs": {"type": "array", "items": {"type": "string"}},
            "bullets": {"type": "array", "items": {"type": "string"}},
            "table": table,
            "sources": {"type": "array", "items": {"type": "string"},
                        "description": "Ids of the sources that support this section."},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "required": ["sections"], "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "sections": {
                "type": "object", "additionalProperties": False,
                "required": [s["key"] for s in guide["sections"] if s["required"]],
                "properties": {s["key"]: section for s in guide["sections"]},
            },
            "sources": {"type": "array", "items": {
                "type": "object", "required": ["id"], "additionalProperties": False,
                "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "url": {"type": "string"}}}},
        },
    }


def llm_instructions(doc_type: str, audience: Optional[str] = None) -> str:
    """Prompt text to give a model so its response fits ``validate_content``."""
    guide = guide_for(doc_type, audience)
    lines = [
        f"Write the content of a {guide['title']} for {guide['audience']}.",
        "",
        "Rules:",
        "- Use only the facts you were given. If something is unknown, say 'Not available'; never estimate or invent figures.",
        "- Put a source id in `sources` for every section that states figures, and list those sources at the top level.",
        "- Respect each section's shape and limits below. Do not add sections that are not listed.",
        "- Reply with ONE JSON object and nothing else.",
        "",
        "Sections:",
    ]
    for s in guide["sections"]:
        limit = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s.items() if k.startswith("max_"))
        shape = {"paragraphs": "paragraphs", "bullets": "bullets", "table": f"table with columns {s.get('columns')}"}[s["shape"]]
        lines.append(f"- {s['key']} ({'required' if s['required'] else 'optional'}; {shape}{'; ' + limit if limit else ''}): "
                     f"{s['purpose']} {s['guidance']}")
    skeleton = {"title": guide["title"], "sections": {s["key"]: {
        {"paragraphs": "paragraphs", "bullets": "bullets", "table": "table"}[s["shape"]]:
            ({"columns": s["columns"], "rows": [["..."] * len(s["columns"])]} if s["shape"] == "table" else ["..."]),
        "sources": ["S1"]} for s in guide["sections"][:2]}, "sources": [{"id": "S1", "title": "...", "url": "..."}]}
    lines += ["", "Response shape (abridged):", json.dumps(skeleton, indent=2)]
    return "\n".join(lines)


# ── Input (what the LLM returned) ──────────────────────────────────────

def parse_llm_response(raw: Any) -> Dict[str, Any]:
    """Accept a dict, JSON text, fenced JSON, or JSON embedded in prose."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        raise ContentError("Content is empty; expected a JSON object.")
    if len(raw.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ContentError(f"Content is larger than {MAX_CONTENT_BYTES // 1000} KB.")
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ContentError("No JSON object found in the response.")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ContentError(f"Response is not valid JSON ({exc.msg} at position {exc.pos}).") from exc
    if not isinstance(value, dict):
        raise ContentError("Response JSON must be an object.")
    return value


@dataclass
class ContentResult:
    doc_type: str
    sections: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[Dict[str, str]] = field(default_factory=list)
    title: str = ""
    audience: str = ""
    density: str = "standard"
    formats: List[str] = field(default_factory=list)
    subtitle: str = ""

    @property
    def errors(self) -> List[Dict[str, str]]:
        return [i for i in self.issues if i["level"] == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.sections)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "doc_type": self.doc_type, "title": self.title, "formats": self.formats,
                "sections": [s["key"] for s in self.sections], "issues": self.issues}

    def build_model(self) -> Dict[str, Any]:
        warnings = ["Content was written by an LLM and has not been verified against recorded evidence."]
        warnings += [f"{i['section'] + ': ' if i['section'] else ''}{i['message']}"
                     for i in self.issues if i["level"] == "warning"]
        return {
            "title": self.title, "subtitle": self.subtitle, "audience": self.audience, "density": self.density,
            "review_status": "review_required", "warnings": warnings, "sections": self.sections,
        }


def _clean(value: Any) -> str:
    return _CONTROL_RE.sub("", str(value if value is not None else "")).strip()


def _as_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def _bullets_from_text(text: str) -> List[str]:
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def _normalise_section(key: str, raw: Any, issues: List[Dict[str, str]]) -> Dict[str, Any]:
    """Lenient shapes in, canonical {paragraphs, bullets, table, sources} out."""
    guide = SECTION_GUIDE[key]
    out: Dict[str, Any] = {"paragraphs": [], "bullets": [], "table": None, "sources": []}
    if isinstance(raw, str):
        if guide["shape"] == "bullets":
            out["bullets"] = _bullets_from_text(_clean(raw))
        else:
            out["paragraphs"] = [p.strip() for p in re.split(r"\n\s*\n", _clean(raw)) if p.strip()]
    elif isinstance(raw, list):
        items = [_clean(x) for x in raw if _clean(x)]
        out["bullets" if guide["shape"] != "paragraphs" else "paragraphs"] = items
    elif isinstance(raw, dict):
        out["paragraphs"] = [_clean(p) for p in _as_list(raw.get("paragraphs", raw.get("text"))) if _clean(p)]
        out["bullets"] = [_clean(b) for b in _as_list(raw.get("bullets", raw.get("items"))) if _clean(b)]
        out["sources"] = [_clean(s) for s in _as_list(raw.get("sources")) if _clean(s)]
        table = raw.get("table")
        if table is not None:
            if not (isinstance(table, dict) and isinstance(table.get("columns"), list) and isinstance(table.get("rows"), list)):
                issues.append({"level": "error", "section": key, "message": "`table` must be {columns: [...], rows: [[...]]}."})
            else:
                columns = [_clean(c) for c in table["columns"]]
                rows = []
                for n, row in enumerate(table["rows"], start=1):
                    cells = [_clean(c) for c in row] if isinstance(row, list) else None
                    if cells is None or len(cells) > len(columns):
                        issues.append({"level": "error", "section": key,
                                       "message": f"Table row {n} does not fit the {len(columns)} columns."})
                        continue
                    rows.append(cells + [""] * (len(columns) - len(cells)))
                out["table"] = {"columns": columns, "rows": rows}
    else:
        issues.append({"level": "error", "section": key, "message": "Section must be text, a list, or an object."})
    return out


def _is_empty(section: Dict[str, Any]) -> bool:
    return not (section["paragraphs"] or section["bullets"] or (section["table"] and section["table"]["rows"]))


def _check_limits(key: str, section: Dict[str, Any], compact: bool, issues: List[Dict[str, str]]) -> None:
    limits = _limits(key, compact)
    words = sum(len(p.split()) for p in section["paragraphs"])
    if "max_words" in limits and words > limits["max_words"]:
        issues.append({"level": "warning", "section": key,
                       "message": f"{words} words exceeds the {limits['max_words']}-word target."})
    if "max_items" in limits and len(section["bullets"]) > limits["max_items"]:
        issues.append({"level": "warning", "section": key,
                       "message": f"{len(section['bullets'])} bullets exceeds the {limits['max_items']}-item target."})
    if "max_rows" in limits and section["table"] and len(section["table"]["rows"]) > limits["max_rows"]:
        issues.append({"level": "warning", "section": key,
                       "message": f"{len(section['table']['rows'])} table rows exceeds the {limits['max_rows']}-row target."})
    if any(len(c) > MAX_CELL_CHARS for r in (section["table"] or {"rows": []})["rows"] for c in r):
        issues.append({"level": "warning", "section": key, "message": f"A table cell exceeds {MAX_CELL_CHARS} characters."})


def validate_content(
    doc_type: str, content: Any, *, formats: Optional[List[str]] = None, audience: Optional[str] = None,
    deal: Optional[Dict[str, Any]] = None,
) -> ContentResult:
    """Parse and check an LLM response against the document type's guide. Nothing is truncated."""
    spec = _doc_type(doc_type)
    compact = spec.get("density") == "compact"
    result = ContentResult(
        doc_type=doc_type, title=spec["title"], audience=audience or spec["audience"],
        density=spec["density"], formats=list(formats or spec["formats"]),
        subtitle=(deal or {}).get("target_company") or (deal or {}).get("name") or "",
    )
    try:
        payload = parse_llm_response(content)
    except ContentError as exc:
        result.issues.append({"level": "error", "section": "", "message": str(exc)})
        return result
    if _clean(payload.get("title")):
        result.title = _clean(payload["title"])[:200]

    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, dict):
        result.issues.append({"level": "error", "section": "", "message": "`sections` must be an object keyed by section name."})
        return result

    allowed = section_keys(doc_type)
    for key in raw_sections:
        if key not in allowed:
            result.issues.append({"level": "warning", "section": key, "message": "Not part of this document type; ignored."})

    source_ids = set()
    top_sources = payload.get("sources")
    source_lines: List[str] = []
    for src in _as_list(top_sources):
        if isinstance(src, dict) and _clean(src.get("id")):
            source_ids.add(_clean(src["id"]))
            title, url = _clean(src.get("title")), _clean(src.get("url"))
            source_lines.append(f"[{_clean(src['id'])}] {title or url}" + (f" ({url})" if title and url else ""))
        elif isinstance(src, str) and _clean(src):
            source_lines.append(_clean(src))

    for key in allowed:
        required = key in spec["required"]
        if key not in raw_sections:
            if key == "sources_methodology" and source_lines:
                continue  # built from the top-level sources below
            if required:
                result.issues.append({"level": "error", "section": key, "message": "Required section is missing."})
            continue
        section = _normalise_section(key, raw_sections[key], result.issues)
        if _is_empty(section):
            if required:
                result.issues.append({"level": "error", "section": key, "message": "Required section is empty."})
            continue
        _check_limits(key, section, compact, result.issues)
        text = " ".join(section["paragraphs"] + section["bullets"]
                        + [c for r in (section["table"] or {"rows": []})["rows"] for c in r])
        if key in _NEEDS_SOURCE and _FIGURE_RE.search(text) and not section["sources"]:
            result.issues.append({"level": "warning", "section": key, "message": "States figures but cites no sources."})
        unknown = [s for s in section["sources"] if source_ids and s not in source_ids]
        if unknown:
            result.issues.append({"level": "warning", "section": key,
                                  "message": f"Cites sources not listed at the top level: {', '.join(unknown)}."})
        result.sections.append(_to_model_section(key, section))

    if source_lines and not any(s["key"] == "sources_methodology" for s in result.sections) \
            and "sources_methodology" in allowed:
        result.sections.append(_to_model_section(
            "sources_methodology", {"paragraphs": [], "bullets": source_lines, "table": None, "sources": []}))
    if not result.sections and not result.errors:
        result.issues.append({"level": "error", "section": "", "message": "No usable sections in the content."})
    return result


def _to_model_section(key: str, section: Dict[str, Any]) -> Dict[str, Any]:
    blocks: List[Dict[str, Any]] = [{"type": "paragraph", "text": p} for p in section["paragraphs"]]
    if section["bullets"]:
        blocks.append({"type": "bullets", "items": section["bullets"]})
    if section["table"] and section["table"]["rows"]:
        blocks.append({"type": "table", "columns": section["table"]["columns"], "rows": section["table"]["rows"]})
    if section["sources"]:
        blocks.append({"type": "paragraph", "label": "Sources", "text": ", ".join(section["sources"])})
    return {"key": key, "title": SECTION_TITLES[key], "blocks": blocks}
