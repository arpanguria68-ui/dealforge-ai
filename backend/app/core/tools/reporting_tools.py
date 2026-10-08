"""
OFAS Reporting Tools — IC Memo, Meeting Memo, Deck Assembly, Compliance QA

MCP Tools:
- generate_ic_memo: Assemble Investment Committee memo (PDF) with citation system
- generate_meeting_memo: Generate meeting minutes/memos for deal discussions
- generate_deal_deck: Assemble pitch deck (PPTX) from agent analysis results
"""

import io
import json
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime
import structlog

from app.core.tools.tool_router import BaseTool, ToolResult

logger = structlog.get_logger()

def _validated(result: "ToolResult", path_key: str, fmt: str) -> "ToolResult":
    """Re-open the written file with its library; a broken file is a failure."""
    if not result.success or not isinstance(result.data, dict):
        return result
    from app.core.reports.report_guardrails import ReportGuardrails

    path = result.data.get(path_key)
    try:
        with open(path, "rb") as handle:
            content = handle.read()
    except OSError as exc:
        return ToolResult(success=False, data=None, error=f"Generated file unreadable: {exc}")
    check = ReportGuardrails.validate_artifact(fmt, content)
    if not check.get("valid"):
        return ToolResult(success=False, data=None,
                          error=f"Generated {fmt.upper()} failed validation: {'; '.join(check.get('issues', []))}")
    result.data["validation"] = check.get("stats", {})
    result.data["built_with"] = "python document libraries"
    return result


from app.core.paths import output_dir

OUTPUT_DIR = output_dir()


# ═══════════════════════════════════════════════
#  1. IC Memo Generator
# ═══════════════════════════════════════════════


class GenerateICMemoTool(BaseTool):
    """
    Generate an Investment Committee memo with structured sections
    and a citation/exhibit system linking back to RAG chunk IDs.

    Output: PDF file (via fpdf2) with appendix of source citations.
    """

    def __init__(self):
        super().__init__(
            name="generate_ic_memo",
            description=(
                "Generate an Investment Committee memo (PDF) with structured sections, "
                "financial exhibits, and a citation system linking to RAG sources."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Company ticker"},
                "deal_name": {"type": "string", "description": "Deal name"},
                "sections": {
                    "type": "object",
                    "description": (
                        "Memo sections: {'executive_summary': '...', "
                        "'investment_thesis': '...', 'financial_analysis': '...', "
                        "'valuation': '...', 'risks': '...', 'recommendation': '...'}"
                    ),
                },
                "exhibits": {
                    "type": "array",
                    "description": "Financial exhibits/tables to include",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "data": {"type": "object"},
                        },
                    },
                },
                "citations": {
                    "type": "array",
                    "description": (
                        "Source citations: [{'id': 'C1', 'source': 'CIM p.12', "
                        "'chunk_id': 'abc123', 'content': '...'}]"
                    ),
                    "items": {"type": "object"},
                },
                "format": {
                    "type": "string",
                    "description": "Output format: pdf, docx, or markdown",
                    "enum": ["pdf", "docx", "markdown"],
                },
            },
            "required": ["ticker", "deal_name", "sections"],
        }

    async def execute(
        self,
        ticker: str = "",
        deal_name: str = "",
        sections: Optional[Dict] = None,
        exhibits: Optional[List[Dict]] = None,
        citations: Optional[List[Dict]] = None,
        agent_results: Optional[List[Dict]] = None,
        format: str = "pdf",
        **kwargs,
    ) -> ToolResult:
        sections = sections or {}
        exhibits = exhibits or []
        citations = citations or []
        agent_results = agent_results or []

        if not sections:
            return ToolResult(
                success=False, data=None, error="At least one memo section is required"
            )

        try:
            if format.lower() == "docx":
                return self._generate_docx_memo(
                    ticker, deal_name, sections, exhibits, citations
                )

            from fpdf import FPDF

            class ICMemo(FPDF):
                def __init__(self, deal_name, ticker):
                    super().__init__()
                    self.deal_name = deal_name
                    self.ticker = ticker

                def header(self):
                    self.set_font("Helvetica", "B", 8)
                    self.set_text_color(100, 100, 100)
                    self.cell(
                        0,
                        5,
                        f"CONFIDENTIAL - {self.deal_name} ({self.ticker})",
                        align="L",
                    )
                    self.ln(3)
                    self.set_draw_color(0, 51, 102)
                    self.line(10, 12, 200, 12)
                    self.ln(5)

                def footer(self):
                    self.set_y(-15)
                    self.set_font("Helvetica", "I", 7)
                    self.set_text_color(150, 150, 150)
                    self.cell(
                        0,
                        10,
                        f"Page {self.page_no()}/{{nb}} | OFAS IC Memo | {datetime.utcnow().strftime('%Y-%m-%d')}",
                        align="C",
                    )

                def section_title(self, title):
                    self.set_font("Helvetica", "B", 14)
                    self.set_text_color(0, 51, 102)
                    self.cell(0, 10, self._clean(title), new_x="LMARGIN", new_y="NEXT")
                    self.set_draw_color(0, 51, 102)
                    self.line(10, self.get_y(), 200, self.get_y())
                    self.ln(3)

                def body_text(self, text):
                    self.set_font("Helvetica", "", 10)
                    self.set_text_color(30, 30, 30)
                    self.multi_cell(0, 5, self._clean(text))
                    self.ln(3)

                def citation_ref(self, citation_id):
                    self.set_font("Helvetica", "", 8)
                    self.set_text_color(0, 100, 200)
                    self.write(4, f" [{citation_id}]")
                    self.set_text_color(30, 30, 30)

                def _clean(self, text):
                    if not text:
                        return ""
                    # Replace common unicode chars that break Helvetica
                    replacements = {
                        "\u2014": "--",  # em dash
                        "\u2013": "-",  # en dash
                        "\u2018": "'",  # left single quote
                        "\u2019": "'",  # right single quote
                        "\u201c": '"',  # left double quote
                        "\u201d": '"',  # right double quote
                        "\u2022": "*",  # bullet
                        "\u2026": "...",  # ellipsis
                        "\u00a0": " ",  # non-breaking space
                    }
                    for old, new in replacements.items():
                        text = text.replace(old, new)
                    return text.encode("latin-1", "replace").decode("latin-1")

            pdf = ICMemo(deal_name, ticker)
            pdf.alias_nb_pages()

            # Cover page
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 28)
            pdf.set_text_color(0, 51, 102)
            pdf.ln(40)
            pdf.cell(
                0, 15, pdf._clean(deal_name), align="C", new_x="LMARGIN", new_y="NEXT"
            )
            pdf.set_font("Helvetica", "", 16)
            pdf.set_text_color(80, 80, 80)
            pdf.cell(
                0,
                10,
                "Investment Committee Memorandum",
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(10)
            pdf.set_font("Helvetica", "", 12)
            pdf.cell(
                0, 8, f"Ticker: {ticker}", align="C", new_x="LMARGIN", new_y="NEXT"
            )
            pdf.cell(
                0,
                8,
                f"Date: {datetime.utcnow().strftime('%B %d, %Y')}",
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.cell(
                0,
                8,
                "Prepared by: OFAS Multi-Agent System",
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )

            # Standard section order
            section_order = [
                ("executive_summary", "1. EXECUTIVE SUMMARY"),
                ("investment_thesis", "2. INVESTMENT THESIS"),
                ("company_overview", "3. COMPANY OVERVIEW"),
                ("market_analysis", "4. MARKET ANALYSIS"),
                ("financial_analysis", "5. FINANCIAL ANALYSIS"),
                ("valuation", "6. VALUATION"),
                ("risks", "7. RISK ASSESSMENT"),
                ("recommendation", "8. RECOMMENDATION"),
            ]

            for key, title in section_order:
                content = sections.get(key)
                if content:
                    pdf.add_page()
                    pdf.section_title(title)
                    pdf.body_text(content)

            # Exhibits
            if exhibits:
                pdf.add_page()
                pdf.section_title("EXHIBITS")
                for i, exhibit in enumerate(exhibits):
                    pdf.set_font("Helvetica", "B", 11)
                    pdf.cell(
                        0,
                        8,
                        pdf._clean(f"Exhibit {i+1}: {exhibit.get('title', '')}"),
                        new_x="LMARGIN",
                        new_y="NEXT",
                    )
                    data = exhibit.get("data", {})
                    if isinstance(data, dict):
                        for k, v in data.items():
                            pdf.set_font("Helvetica", "", 9)
                            pdf.cell(
                                0,
                                5,
                                pdf._clean(f"  {k}: {v}"),
                                new_x="LMARGIN",
                                new_y="NEXT",
                            )
                    pdf.ln(5)

            # Citations appendix
            if citations:
                pdf.add_page()
                pdf.section_title("SOURCE CITATIONS")
                for cit in citations:
                    cit_id = cit.get("id", "?")
                    source = cit.get("source", "Unknown")
                    chunk_id = cit.get("chunk_id", "")
                    content_preview = cit.get("content", "")[:200]

                    pdf.set_font("Helvetica", "B", 9)
                    pdf.cell(
                        0,
                        5,
                        pdf._clean(f"[{cit_id}] {source}"),
                        new_x="LMARGIN",
                        new_y="NEXT",
                    )
                    if chunk_id:
                        pdf.set_font("Helvetica", "I", 8)
                        pdf.cell(
                            0,
                            4,
                            pdf._clean(f"  RAG chunk: {chunk_id}"),
                            new_x="LMARGIN",
                            new_y="NEXT",
                        )
                    if content_preview:
                        pdf.set_font("Helvetica", "", 8)
                        pdf.multi_cell(0, 4, pdf._clean(f'  "{content_preview}"'))
                    pdf.ln(2)

            # Save
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{ticker}_IC_Memo_{timestamp}.pdf"
            output_path = OUTPUT_DIR / filename
            pdf.output(str(output_path))

            return _validated(ToolResult(
                success=True,
                data={
                    "memo_path": str(output_path),
                    "file_size_kb": round(output_path.stat().st_size / 1024, 1),
                    "sections_included": [k for k, _ in section_order if k in sections],
                    "exhibit_count": len(exhibits),
                    "citation_count": len(citations),
                    "pages": pdf.page_no(),
                },
            ), "memo_path", "pdf")

        except ImportError:
            # PDF library missing: build a real DOCX with python-docx instead of
            # passing a Markdown text file off as the memo.
            logger.warning("fpdf2 unavailable; building IC memo as DOCX")
            return self._generate_docx_memo(ticker, deal_name, sections, exhibits, citations)
        except Exception as e:
            logger.error("IC memo generation failed", error=str(e))
            return ToolResult(success=False, data=None, error=str(e))

    def _generate_docx_memo(
        self, ticker, deal_name, sections, exhibits, citations
    ) -> ToolResult:
        """Generate IC Memo in DOCX format using python-docx."""
        try:
            from docx import Document
            from docx.shared import Pt, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            doc = Document()
            style = doc.styles["Normal"]
            style.font.name = "Calibri"
            style.font.size = Pt(11)

            # Header
            header = doc.add_paragraph()
            header.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = header.add_run(f"CONFIDENTIAL - {deal_name} ({ticker})")
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

            # Title page
            for _ in range(5):
                doc.add_paragraph()

            p_title = doc.add_paragraph()
            p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run_title = p_title.add_run(deal_name)
            run_title.font.size = Pt(28)
            run_title.font.bold = True
            run_title.font.color.rgb = RGBColor(0x00, 0x33, 0x66)

            p_sub = doc.add_paragraph()
            p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run_sub = p_sub.add_run("Investment Committee Memorandum")
            run_sub.font.size = Pt(18)
            run_sub.font.color.rgb = RGBColor(0x50, 0x50, 0x50)

            doc.add_paragraph()
            p_meta = doc.add_paragraph()
            p_meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_meta.add_run(f"Ticker: {ticker}\nDate: {datetime.utcnow().strftime('%B %d, %Y')}\nPrepared by: OFAS Multi-Agent System")

            doc.add_page_break()

            # Sections
            section_order = [
                ("executive_summary", "1. EXECUTIVE SUMMARY"),
                ("investment_thesis", "2. INVESTMENT THESIS"),
                ("company_overview", "3. COMPANY OVERVIEW"),
                ("market_analysis", "4. MARKET ANALYSIS"),
                ("financial_analysis", "5. FINANCIAL ANALYSIS"),
                ("valuation", "6. VALUATION"),
                ("risks", "7. RISK ASSESSMENT"),
                ("recommendation", "8. RECOMMENDATION"),
            ]

            for key, title in section_order:
                content = sections.get(key)
                if content:
                    doc.add_heading(title, level=1)
                    doc.add_paragraph(content)

            # Exhibits
            if exhibits:
                doc.add_page_break()
                doc.add_heading("EXHIBITS", level=1)
                for i, exhibit in enumerate(exhibits):
                    doc.add_heading(f"Exhibit {i+1}: {exhibit.get('title', '')}", level=2)
                    data = exhibit.get("data", {})
                    if isinstance(data, dict):
                        table = doc.add_table(rows=len(data), cols=2)
                        for j, (k, v) in enumerate(data.items()):
                            table.rows[j].cells[0].text = str(k)
                            table.rows[j].cells[1].text = str(v)

            # Citations
            if citations:
                doc.add_page_break()
                doc.add_heading("SOURCE CITATIONS", level=1)
                for cit in citations:
                    p = doc.add_paragraph(style="List Bullet")
                    run = p.add_run(f"[{cit.get('id', '?')}] {cit.get('source', 'Unknown')}")
                    run.font.bold = True
                    if cit.get("chunk_id"):
                        doc.add_paragraph(f"RAG chunk: {cit.get('chunk_id')}", style="List Bullet")
                    if cit.get("content"):
                        doc.add_paragraph(f'"{cit.get("content", "")[:300]}"', style="List Bullet")

            # Save
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{ticker}_IC_Memo_{timestamp}.docx"
            output_path = OUTPUT_DIR / filename
            doc.save(str(output_path))

            return _validated(ToolResult(
                success=True,
                data={
                    "memo_path": str(output_path),
                    "file_size_kb": round(output_path.stat().st_size / 1024, 1),
                    "format": "docx",
                    "pages": "N/A",
                },
            ), "memo_path", "docx")
        except Exception as e:
            logger.error("IC memo DOCX generation failed", error=str(e))
            return ToolResult(success=False, data=None, error=f"IC memo DOCX generation failed: {e}")


# ═══════════════════════════════════════════════
#  2. Deal Deck Assembly (PPTX)
# ═══════════════════════════════════════════════


class GenerateDealDeckTool(BaseTool):
    """
    Assemble a deal pitch deck from agent analysis results.
    Leverages the existing report_generator.generate_pptx.
    """

    def __init__(self):
        super().__init__(
            name="generate_deal_deck",
            description=(
                "Assemble a deal pitch deck (PPTX) from agent analysis results. "
                "McKinsey-style formatting with executive summary, financials, "
                "valuation, and risk slides."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "deal_name": {"type": "string"},
                "deal_data": {
                    "type": "object",
                    "description": "Deal metadata (company_name, deal_type, etc.)",
                },
                "analyst_data": {
                    "type": "object",
                    "description": "Financial analysis data",
                },
                "agent_results": {
                    "type": "array",
                    "description": "List of agent outputs to include",
                },
            },
            "required": ["ticker", "deal_name"],
        }

    async def execute(
        self,
        ticker: str = "",
        deal_name: str = "",
        deal_data: Optional[Dict] = None,
        analyst_data: Optional[Dict] = None,
        agent_results: Optional[List[Dict]] = None,
        **kwargs,
    ) -> ToolResult:
        deal_data = deal_data or {"company_name": deal_name, "deal_type": "acquisition"}
        analyst_data = analyst_data or {}
        agent_results = agent_results or []

        try:
            from app.core.reports.report_generator import generate_pptx

            pptx_bytes = generate_pptx(deal_data, analyst_data, agent_results)

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{ticker}_Deal_Deck_{timestamp}.pptx"
            output_path = OUTPUT_DIR / filename

            with open(str(output_path), "wb") as f:
                f.write(
                    pptx_bytes.getvalue()
                    if hasattr(pptx_bytes, "getvalue")
                    else pptx_bytes
                )

            return _validated(ToolResult(
                success=True,
                data={
                    "deck_path": str(output_path),
                    "file_size_kb": round(output_path.stat().st_size / 1024, 1),
                    "format": "pptx",
                    "agent_sections": len(agent_results),
                },
            ), "deck_path", "pptx")

        except ImportError:
            return ToolResult(
                success=False,
                data=None,
                error="python-pptx not installed. Run: pip install python-pptx",
            )
        except Exception as e:
            logger.error("Deck generation failed", error=str(e))
            return ToolResult(success=False, data=None, error=str(e))


# ═══════════════════════════════════════════════
#  3. Meeting Memo Generator
# ═══════════════════════════════════════════════


class GenerateMeetingMemoTool(BaseTool):
    """
    Generate a Meeting Memo for deal discussions, IC meetings, and due diligence sessions.
    Supports PDF, PPTX, and HTML output formats.
    
    Output: Professional meeting memo with attendees, agenda, discussion points,
    decisions, and action items.
    """

    def __init__(self):
        super().__init__(
            name="generate_meeting_memo",
            description=(
                "Generate a Meeting Memo (PDF/PPTX/HTML) for deal discussions. "
                "Includes attendees, agenda, agent analysis summary, decisions, "
                "and action items with owners and due dates."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "deal_name": {"type": "string", "description": "Deal name"},
                "meeting_type": {"type": "string", "description": "Type of meeting (IC Review, Due Diligence, etc.)"},
                "meeting_date": {"type": "string", "description": "Meeting date (YYYY-MM-DD)"},
                "location": {"type": "string", "description": "Meeting location (Virtual, Office, etc.)"},
                "duration": {"type": "string", "description": "Meeting duration (e.g., '60 min')"},
                "attendees": {
                    "type": "array",
                    "description": "List of attendees [{name, role, organization}]",
                    "items": {"type": "object"},
                },
                "agenda": {
                    "type": "array",
                    "description": "Agenda items",
                    "items": {"type": "string"},
                },
                "agent_results": {
                    "type": "array",
                    "description": "Agent analysis outputs to include",
                },
                "discussion_summary": {
                    "type": "object",
                    "description": "Discussion points: {key_points: [], questions: [], concerns: []}",
                },
                "decisions": {
                    "type": "array",
                    "description": "Decisions made: [{description, made_by, outcome}]",
                    "items": {"type": "object"},
                },
                "action_items": {
                    "type": "array",
                    "description": "Action items: [{description, owner, due_date, status}]",
                    "items": {"type": "object"},
                },
                "format": {
                    "type": "string",
                    "description": "Output format: pdf, docx, pptx, or html",
                    "enum": ["pdf", "docx", "pptx", "html"],
                },
            },
            "required": ["deal_name", "meeting_type"],
        }

    async def execute(
        self,
        deal_name: str = "",
        meeting_type: str = "Deal Review",
        meeting_date: str = "",
        location: str = "Virtual",
        duration: str = "60 min",
        attendees: Optional[List[Dict]] = None,
        agenda: Optional[List[str]] = None,
        agent_results: Optional[List[Dict]] = None,
        discussion_summary: Optional[Dict] = None,
        decisions: Optional[List[Dict]] = None,
        action_items: Optional[List[Dict]] = None,
        format: str = "pdf",
        **kwargs,
    ) -> ToolResult:
        attendees = attendees or []
        agenda = agenda or []
        agent_results = agent_results or []
        decisions = decisions or []
        action_items = action_items or []
        
        if not meeting_date:
            meeting_date = datetime.now().strftime("%Y-%m-%d")

        try:
            from app.core.reports.meeting_memo import generate_meeting_memo_report

            # Build deal and meeting_info dicts
            deal = {"name": deal_name}
            meeting_info = {
                "meeting_type": meeting_type,
                "date": meeting_date,
                "location": location,
                "duration": duration,
                "attendees": attendees,
                "agenda": agenda,
            }

            # Generate the memo
            memo_bytes = generate_meeting_memo_report(
                deal=deal,
                meeting_info=meeting_info,
                agent_results=agent_results,
                format=format,
                discussion_summary=discussion_summary,
                decisions=decisions,
                action_items=action_items,
            )

            # Save to file
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            ext = format.lower()
            filename = f"{deal_name.replace(' ', '_')}_MeetingMemo_{timestamp}.{ext}"
            output_path = OUTPUT_DIR / filename

            with open(str(output_path), "wb") as f:
                f.write(memo_bytes)

            return _validated(ToolResult(
                success=True,
                data={
                    "memo_path": str(output_path),
                    "file_size_kb": round(output_path.stat().st_size / 1024, 1),
                    "format": format,
                    "meeting_type": meeting_type,
                    "attendee_count": len(attendees),
                    "agenda_items": len(agenda),
                    "decision_count": len(decisions),
                    "action_item_count": len(action_items),
                },
            ), "memo_path", ext)

        except ImportError as e:
            return ToolResult(
                success=False,
                data=None,
                error=f"Missing dependency: {str(e)}. Install required packages.",
            )
        except Exception as e:
            logger.error("Meeting memo generation failed", error=str(e))
            return ToolResult(success=False, data=None, error=str(e))
