"""Render markdown text and structured agent output into python-docx documents.

Agents return either markdown prose (the IC memo, the chat synthesis) or nested JSON
(financials, valuation, risks). Dumping either as plain paragraphs produced literal
``## `` and ``**`` characters and one-line "Completed: ..." stubs instead of findings.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.shared import Pt, RGBColor

_INLINE = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|`[^`\n]+`)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^(\s*)[-*+•]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
# Markup some models emit for chat widgets; meaningless in a document.
_WIDGET = re.compile(r"<(FinanceWidget|ElicitationsGroup|Elicitation)\b.*?</\1>|<(FinanceWidget|ElicitationsGroup|Elicitation)\b[^>]*/?>", re.DOTALL)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Raw tool dumps, internals and duplicates: not findings a reader should wade through.
SKIP_KEYS: Set[str] = {
    "_validation", "raw_response", "raw", "raw_reasoning", "raw_synthesis", "tool_results", "tool_calls",
    "calculations", "deterministic_calculations", "laya_quality_gate", "confidence_basis",
    "synthesis_status", "provider_warning", "files", "charts", "sections", "file_status",
    "file_sections", "file_review_status", "human_review_required", "inputs", "retrieved_at",
    "data_source", "source_url", "report_version", "analysis_fingerprint",
}
# Financial-statement figures: shown, but flagged when no recorded source backs the value.
_STATEMENT_KEYS = {
    "revenue", "annual_revenue", "gross_profit", "operating_income", "net_income", "ebitda",
    "free_cash_flow", "operating_cash_flow", "capital_expenditures", "cash", "long_term_debt", "total_debt",
}
UNVERIFIED_NOTE = " (unverified: no linked source)"
_PERCENT_TOKENS = {"percent", "pct", "margin", "growth", "wacc", "irr", "yield", "weight", "rate", "ratio", "cagr"}
_MULTIPLE_TOKENS = {"multiple", "moic"}


def _tokens(key: Any) -> Set[str]:
    return set(re.split(r"[^a-z0-9]+", str(key).lower()))


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as cp1252 upstream ("â€”" -> "—")."""
    if "â€" not in text and "Ã" not in text:
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text.replace("â€”", "—").replace("â€“", "–").replace("â€™", "’").replace("â€œ", "“").replace("â€", "”")


def clean(text: Any) -> str:
    """Printable text; unlike the PDF path, Word handles Unicode so it is kept."""
    return _CONTROL.sub("", _fix_mojibake("" if text is None else str(text)))


def add_inline(paragraph, text: str, size: Optional[float] = None) -> None:
    """Add runs for **bold**, *italic* and `code` spans."""
    for part in _INLINE.split(clean(text)):
        if not part:
            continue
        if (part.startswith("**") and part.endswith("**") and len(part) > 4) or (
                part.startswith("__") and part.endswith("__") and len(part) > 4):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            run = paragraph.add_run(part[1:-1])
            run.italic = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Consolas"
        else:
            run = paragraph.add_run(part)
        if size:
            run.font.size = Pt(size)


def _add_table(doc, rows: List[List[str]]) -> None:
    width = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=width, style="Table Grid")
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r_index, row in enumerate(rows):
        cells = table.add_row().cells
        for c_index in range(width):
            cell = cells[c_index]
            cell.text = ""
            add_inline(cell.paragraphs[0], row[c_index] if c_index < len(row) else "", size=9)
            if r_index == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True
    doc.add_paragraph()


def add_markdown(doc, text: str, heading_offset: int = 2, max_heading: int = 4) -> None:
    """Render markdown into Word headings, lists, tables and styled runs.

    ``heading_offset`` shifts ``#`` down (a ``#`` inside a section becomes a level-3 heading).
    """
    text = _WIDGET.sub("", clean(text))
    lines = text.splitlines()
    paragraph_buffer: List[str] = []
    i = 0

    def flush() -> None:
        if paragraph_buffer:
            para = doc.add_paragraph()
            add_inline(para, " ".join(s.strip() for s in paragraph_buffer))
            paragraph_buffer.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("```"):
            flush()
            i += 1
            block: List[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            if any(b.strip() for b in block):
                para = doc.add_paragraph()
                run = para.add_run("\n".join(block))
                run.font.name = "Consolas"
                run.font.size = Pt(8.5)
        elif re.fullmatch(r"[-*_]{3,}", stripped):
            flush()
        elif _HEADING.match(stripped):
            flush()
            marks, title = _HEADING.match(stripped).groups()
            level = min(len(marks) + heading_offset - 1, max_heading)
            doc.add_heading(re.sub(r"[*_`]", "", title), level=max(level, 1))
        elif _TABLE_ROW.match(stripped):
            flush()
            rows: List[List[str]] = []
            while i < len(lines) and _TABLE_ROW.match(lines[i].strip()):
                if not _TABLE_SEP.match(lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            i -= 1
            if rows:
                _add_table(doc, rows)
        elif _BULLET.match(line) or _NUMBERED.match(line):
            flush()
            m = _BULLET.match(line)
            numbered = m is None
            m = m or _NUMBERED.match(line)
            depth = min(len(m.group(1).replace("\t", "    ")) // 2, 2)
            style = ("List Number" if numbered else "List Bullet") + (f" {depth + 1}" if depth else "")
            try:
                para = doc.add_paragraph(style=style)
            except KeyError:
                para = doc.add_paragraph(style="List Number" if numbered else "List Bullet")
            add_inline(para, m.group(2))
        elif stripped.startswith(">"):
            flush()
            para = doc.add_paragraph()
            para.paragraph_format.left_indent = Pt(18)
            run_text = stripped.lstrip("> ").strip()
            add_inline(para, run_text)
            for run in para.runs:
                run.italic = True
                run.font.color.rgb = RGBColor(0x50, 0x50, 0x50)
        else:
            paragraph_buffer.append(line)
        i += 1
    flush()


# ───────────────────────── structured payloads ─────────────────────────

def label(key: str) -> str:
    text = re.sub(r"[_\-]+", " ", str(key)).strip()
    text = re.sub(r"\byoy\b", "YoY", text, flags=re.IGNORECASE)
    words = [w.upper() if w.lower() in {"dcf", "lbo", "ebitda", "wacc", "irr", "moic", "fcf", "sec", "ev", "sbc", "eps", "api", "tam", "sam", "som", "arr", "nrr", "ttm", "ipo", "ceo"}
             else w.capitalize() for w in text.split()]
    return " ".join(words)


def format_value(key: str, value: Any) -> str:
    """Human formatting for a scalar, guided by its key (percent, multiple, money)."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        k = str(key).lower()
        tokens = _tokens(k)
        if tokens & _MULTIPLE_TOKENS or "ev_revenue" in k or "ev_ebitda" in k:
            return f"{value:,.2f}x"
        if (tokens & _PERCENT_TOKENS or "cost_of" in k) and "per" not in tokens:
            return f"{value * 100:.2f}%" if abs(value) <= 1.0 else f"{value:.2f}%"
        if abs(value) >= 1e9:
            return f"${value / 1e9:,.2f}B"
        if abs(value) >= 1e6:
            return f"${value / 1e6:,.2f}M"
        if abs(value) >= 1e4:
            return f"{value:,.0f}"
        return f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"
    return clean(value)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def _is_verified(value: Any, verified: Optional[Set[float]]) -> bool:
    """True when the number matches a source-linked figure (any common scale), or no check applies."""
    if verified is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    for candidate in verified:
        for scale in (1.0, 1e3, 1e6, 1e9):
            if abs(value - candidate / scale) <= max(0.011, abs(value) * 0.005):
                return True
    return False


def _bold_label(para, text: str) -> None:
    run = para.add_run(text)
    run.bold = True


def _render_scalar_line(
    doc, key: str, value: Any, style: Optional[str] = None, verified: Optional[Set[float]] = None
) -> None:
    para = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    _bold_label(para, f"{label(key)}: ")
    add_inline(para, format_value(key, value))
    if str(key).lower() in _STATEMENT_KEYS and not _is_verified(value, verified):
        note = para.add_run(UNVERIFIED_NOTE)
        note.italic = True
        note.font.color.rgb = RGBColor(0xB0, 0x60, 0x00)


def _render_list(doc, key: str, items: List[Any], depth: int, verified: Optional[Set[float]] = None) -> None:
    items = [i for i in items if not _empty(i)]
    if not items:
        return
    if all(_is_scalar(i) for i in items):
        for item in items[:25]:
            add_inline(doc.add_paragraph(style="List Bullet"), format_value(key, item) if not isinstance(item, str) else item)
        return
    dicts = [i for i in items if isinstance(i, dict)]
    flat = len(dicts) == len(items) and all(all(_is_scalar(v) or _empty(v) for v in d.values()) for d in dicts)
    columns: List[str] = []
    for d in dicts:
        for k in d:
            if k not in SKIP_KEYS and k not in columns and not all(_empty(x.get(k)) for x in dicts):
                columns.append(k)
    if flat and len(columns) > 7 and 2 <= len(dicts) <= 8:
        title_key = next((k for k in ("fiscal_year", "period", "year", "name", "title") if k in columns), None)
        fields = [c for c in columns if c != title_key]
        header = [""] + [format_value(title_key, d.get(title_key)) if title_key else f"#{n}" for n, d in enumerate(dicts, 1)]
        rows = [header] + [
            [label(f)] + [format_value(f, d.get(f)) if not _empty(d.get(f)) else "-" for d in dicts] for f in fields
        ]
        _add_table(doc, rows)
        return
    if flat and 1 < len(columns) <= 7:
        rows = [[label(c) for c in columns]] + [
            [format_value(c, d.get(c)) if not _empty(d.get(c)) else "-" for c in columns] for d in dicts[:25]
        ]
        _add_table(doc, rows)
        return
    for n, item in enumerate(items[:20], 1):
        if isinstance(item, dict):
            title = next((str(item[k]) for k in ("title", "name", "risk", "metric", "year", "period", "method", "agent") if item.get(k)), f"Item {n}")
            add_inline(doc.add_paragraph(style="List Bullet"), f"**{title}**")
            render_payload(doc, {k: v for k, v in item.items() if str(item.get("title", "")) != str(v) or k != "title"},
                           depth=depth + 1, bullets=True, verified=verified)
        else:
            add_inline(doc.add_paragraph(style="List Bullet"), str(item))


def maybe_json(text: str) -> Any:
    """A dict/list when an agent returned its JSON answer as a string (optionally fenced)."""
    candidate = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    if not candidate.startswith(("{", "[")):
        return None
    try:
        import json

        parsed = json.loads(candidate)
    except ValueError:
        return None
    return parsed if isinstance(parsed, (dict, list)) and parsed else None


def tool_request_note(data: Any) -> Optional[str]:
    """Explain an agent whose final answer was an unexecuted tool request, not an analysis."""
    if isinstance(data, dict) and isinstance(data.get("tool"), str) and isinstance(data.get("arguments"), dict):
        return (f"This agent returned a request to call '{data['tool']}' instead of an analysis, "
                "so it recorded no findings. Re-run it to obtain a risk assessment.")
    return None


def render_payload(
    doc, data: Any, depth: int = 0, bullets: bool = False, skip: Iterable[str] = (),
    verified: Optional[Set[float]] = None,
) -> None:
    """Render nested agent output; depth 0 keys become headings, deeper ones labelled lines.

    ``verified`` holds source-linked figures; statement metrics outside it are flagged unverified.
    ``None`` disables the check.
    """
    if not isinstance(data, dict):
        if not _empty(data):
            add_markdown(doc, str(data), heading_offset=3)
        return
    note = tool_request_note(data) if depth == 0 else None
    if note:
        para = doc.add_paragraph()
        run = para.add_run(note)
        run.italic = True
        run.font.color.rgb = RGBColor(0xB0, 0x60, 0x00)
        return
    skip = set(skip) | SKIP_KEYS
    for key, value in data.items():
        if key in skip or _empty(value):
            continue
        style = "List Bullet" if bullets else None
        if isinstance(value, str) and maybe_json(value) is not None:
            value = maybe_json(value)
        if _is_scalar(value):
            text = str(value)
            if isinstance(value, str) and (len(text) > 220 or "\n" in text):
                if depth == 0:
                    doc.add_heading(label(key), level=3)
                else:
                    _bold_label(doc.add_paragraph(style=style), f"{label(key)}:")
                add_markdown(doc, text, heading_offset=4)
            else:
                _render_scalar_line(doc, key, value, style, verified)
        elif isinstance(value, dict):
            if depth == 0:
                doc.add_heading(label(key), level=3)
            else:
                _bold_label(doc.add_paragraph(style=style), f"{label(key)}:")
            if depth < 3:
                render_payload(doc, value, depth=depth + 1, bullets=depth >= 1, skip=skip, verified=verified)
        elif isinstance(value, list):
            if depth == 0:
                doc.add_heading(label(key), level=3)
            else:
                _bold_label(doc.add_paragraph(style=style), f"{label(key)}:")
            _render_list(doc, key, value, depth, verified)


def summarize_results(data: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the fields an agent's section should lead with (kept for tests/callers)."""
    return {k: v for k, v in data.items() if k not in SKIP_KEYS and not _empty(v)}


# ───────────────────────── plain lines (PDF / PPTX / XLSX) ─────────────────────────

def _strip_inline(text: str) -> str:
    return re.sub(r"(\*\*|__|`)", "", re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", r"\1", clean(text))).strip()


def markdown_lines(text: str) -> List[Tuple[str, int, str]]:
    """(kind, depth, text) from markdown: kind is 'h' (heading), 'b' (bullet) or 'p'."""
    out: List[Tuple[str, int, str]] = []
    in_code = False
    for raw in _WIDGET.sub("", clean(text)).splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped or re.fullmatch(r"[-*_]{3,}", stripped) or _TABLE_SEP.match(stripped):
            continue
        heading = _HEADING.match(stripped)
        bullet = _BULLET.match(line) or _NUMBERED.match(line)
        if heading:
            out.append(("h", 0, _strip_inline(heading.group(2))))
        elif bullet:
            depth = min(len(bullet.group(1).replace("\t", "    ")) // 2, 2)
            out.append(("b", depth, _strip_inline(bullet.group(2))))
        elif _TABLE_ROW.match(stripped):
            out.append(("p", 0, " | ".join(_strip_inline(c) for c in stripped.strip("|").split("|"))))
        else:
            out.append(("p", 0, _strip_inline(stripped)))
    return out


def payload_lines(data: Any, verified: Optional[Set[float]] = None, depth: int = 0) -> List[Tuple[str, int, str]]:
    """(kind, depth, text) lines for a nested agent payload, same skipping/formatting as Word."""
    out: List[Tuple[str, int, str]] = []
    if not isinstance(data, dict):
        return markdown_lines(str(data)) if not _empty(data) else out
    note = tool_request_note(data) if depth == 0 else None
    if note:
        return [("p", 0, note)]
    for key, value in data.items():
        if key in SKIP_KEYS or _empty(value):
            continue
        if isinstance(value, str) and maybe_json(value) is not None:
            value = maybe_json(value)
        if _is_scalar(value):
            text = str(value)
            if isinstance(value, str) and (len(text) > 220 or "\n" in text):
                out.append(("h" if depth == 0 else "b", depth, label(key)))
                out.extend((kind, d + depth + 1, t) for kind, d, t in markdown_lines(text)[:12])
            else:
                note = UNVERIFIED_NOTE if str(key).lower() in _STATEMENT_KEYS and not _is_verified(value, verified) else ""
                out.append(("b" if depth else "p", depth, f"{label(key)}: {format_value(key, value)}{note}"))
        elif isinstance(value, dict):
            out.append(("h" if depth == 0 else "b", depth, label(key)))
            if depth < 3:
                out.extend(payload_lines(value, verified, depth + 1))
        elif isinstance(value, list):
            out.append(("h" if depth == 0 else "b", depth, label(key)))
            for item in [i for i in value if not _empty(i)][:15]:
                if isinstance(item, dict):
                    parts = [f"{label(k)}: {format_value(k, v)}" for k, v in item.items()
                             if k not in SKIP_KEYS and _is_scalar(v) and not _empty(v)]
                    out.append(("b", depth + 1, "; ".join(parts)[:400]))
                else:
                    out.append(("b", depth + 1, format_value(key, item) if not isinstance(item, str) else _strip_inline(item)))
    return out


def agent_lines(result: Dict[str, Any], verified: Optional[Set[float]] = None, limit: int = 60) -> List[Tuple[str, int, str]]:
    """What a report should say about one agent: the memo, else its payload, else its reasoning."""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if isinstance(data.get("memo"), str) and data["memo"].strip():
        lines = markdown_lines(data["memo"])
    elif data:
        lines = payload_lines(data, verified)
    else:
        lines = markdown_lines(result.get("reasoning") or result.get("summary") or "No analysis data available.")
    if len(lines) > limit:
        lines = lines[:limit] + [("p", 0, f"... {len(lines) - limit} more lines in the Word report.")]
    return lines
