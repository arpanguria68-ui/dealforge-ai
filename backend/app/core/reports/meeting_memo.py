"""
Meeting Memo Generator
Generates formatted meeting minutes/memos for deal discussions, IC meetings, and due diligence sessions.
"""

import io
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
import structlog

logger = structlog.get_logger()


def _clean_text(text: Any) -> str:
    """Sanitize text for output formats."""
    if text is None:
        return ""
    s = str(text)
    replacements = {
        "\u2013": "-",
        "\u2014": "--",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "*",
        "\u2026": "...",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return "".join(c for c in s if ord(c) < 128 or c.isprintable())


# ───────────────────────────────────────────────
#  Meeting Memo Generator (PDF)
# ───────────────────────────────────────────────


def generate_meeting_memo(
    deal: Dict,
    meeting_info: Dict,
    agent_results: List[Dict],
    discussion_summary: Optional[Dict] = None,
    decisions: Optional[List[Dict]] = None,
    action_items: Optional[List[Dict]] = None,
) -> bytes:
    """
    Generate a professional Meeting Memo in PDF format.
    
    Args:
        deal: Deal metadata (name, target_company, industry, etc.)
        meeting_info: Meeting details (date, attendees, meeting_type, agenda)
        agent_results: List of agent analysis outputs to include in discussion
        discussion_summary: Key discussion points from the meeting
        decisions: List of decisions made
        action_items: List of action items with owners and due dates
    
    Returns:
        PDF bytes
    """
    try:
        from fpdf import FPDF

        class MeetingMemoPDF(FPDF):
            def __init__(self):
                super().__init__()
                self.set_auto_page_break(auto=True, margin=15)

            def header(self):
                self.set_font("Helvetica", "B", 10)
                self.set_text_color(100, 100, 100)
                self.cell(0, 5, "DEALFORGE AI - CONFIDENTIAL", align="L")
                self.cell(0, 5, f"Generated: {datetime.now().strftime('%Y-%m-%d')}", align="R")
                self.ln(5)
                self.set_draw_color(0, 51, 102)
                self.line(10, 18, 200, 18)
                self.ln(5)

            def section_header(self, title: str, level: int = 1):
                """Add a section header with underline."""
                if level == 1:
                    self.set_font("Helvetica", "B", 14)
                    self.set_text_color(0, 51, 102)
                else:
                    self.set_font("Helvetica", "B", 12)
                    self.set_text_color(50, 50, 50)
                
                self.cell(0, 8, _clean_text(title), new_x="LMARGIN", new_y="NEXT")
                self.set_draw_color(200, 200, 200)
                if level == 1:
                    self.line(10, self.get_y(), 200, self.get_y())
                self.ln(3)

            def body_text(self, text: str, indent: int = 0):
                """Add body text with optional indentation."""
                self.set_font("Helvetica", "", 10)
                self.set_text_color(30, 30, 30)
                if indent > 0:
                    self.set_x(10 + indent)
                self.multi_cell(0, 5, _clean_text(text))
                self.ln(2)

            def bullet_item(self, text: str, indent: int = 10):
                """Add a bullet point."""
                self.set_font("Helvetica", "", 10)
                self.set_text_color(30, 30, 30)
                self.set_x(10 + indent)
                self.cell(5, 5, "*", 0, 0)
                self.cell(0, 5, _clean_text(text), new_x="LMARGIN", new_y="NEXT")
                self.ln(1)

            def key_value(self, key: str, value: str, bold_key: bool = True):
                """Add a key-value pair."""
                if bold_key:
                    self.set_font("Helvetica", "B", 10)
                else:
                    self.set_font("Helvetica", "", 10)
                self.set_text_color(30, 30, 30)
                self.cell(40, 5, _clean_text(key), 0, 0)
                self.set_font("Helvetica", "", 10)
                self.cell(0, 5, _clean_text(value), 0, 1)

            def add_table_row(self, cols: List[str], col_widths: List[float], bold_first: bool = False):
                """Add a table row."""
                for i, col in enumerate(cols):
                    if bold_first and i == 0:
                        self.set_font("Helvetica", "B", 9)
                    else:
                        self.set_font("Helvetica", "", 9)
                    self.cell(col_widths[i], 5, _clean_text(str(col)[:50]), 1, 0, "L")
                self.ln()

        pdf = MeetingMemoPDF()
        pdf.alias_nb_pages()

        # ─────────────────────────────────────────────
        #  Cover Page
        # ─────────────────────────────────────────────
        pdf.add_page()
        
        # Meeting title
        meeting_type = meeting_info.get("meeting_type", "Deal Review")
        deal_name = deal.get("name", "Untitled Deal")
        
        pdf.set_font("Helvetica", "B", 24)
        pdf.set_text_color(0, 51, 102)
        pdf.ln(30)
        pdf.cell(0, 15, f"Meeting Memo: {meeting_type}", align="C", new_x="LMARGIN", new_y="NEXT")
        
        pdf.set_font("Helvetica", "", 16)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 10, deal_name, align="C", new_x="LMARGIN", new_y="NEXT")
        
        # Meeting metadata
        pdf.ln(20)
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(60, 60, 60)
        
        meeting_date = meeting_info.get("date", datetime.now().strftime("%Y-%m-%d"))
        pdf.cell(0, 8, f"Date: {meeting_date}", align="C", new_x="LMARGIN", new_y="NEXT")
        
        location = meeting_info.get("location", "Virtual")
        pdf.cell(0, 8, f"Location: {location}", align="C", new_x="LMARGIN", new_y="NEXT")
        
        duration = meeting_info.get("duration", "TBD")
        pdf.cell(0, 8, f"Duration: {duration}", align="C", new_x="LMARGIN", new_y="NEXT")

        # ─────────────────────────────────────────────
        #  Attendees Section
        # ─────────────────────────────────────────────
        pdf.add_page()
        pdf.section_header("ATTENDEES", level=1)
        
        attendees = meeting_info.get("attendees", [])
        if attendees:
            for attendee in attendees:
                name = attendee.get("name", "Unknown")
                role = attendee.get("role", "")
                org = attendee.get("organization", "")
                
                pdf.set_font("Helvetica", "B", 10)
                pdf.set_text_color(0, 51, 102)
                pdf.cell(0, 6, f"• {name}", new_x="LMARGIN", new_y="NEXT")
                
                if role or org:
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(100, 100, 100)
                    details = f"{role}" + (f" - {org}" if org else "")
                    pdf.cell(0, 5, details, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(3)
        else:
            pdf.body_text("No attendees recorded.")

        # ─────────────────────────────────────────────
        #  Agenda Section
        # ─────────────────────────────────────────────
        pdf.add_page()
        pdf.section_header("AGENDA", level=1)
        
        agenda = meeting_info.get("agenda", [])
        if agenda:
            for idx, item in enumerate(agenda, 1):
                pdf.bullet_item(f"{idx}. {item}")
        else:
            pdf.body_text("No agenda items recorded.")

        # ─────────────────────────────────────────────
        #  Agent Analysis Summary Section
        # ─────────────────────────────────────────────
        pdf.add_page()
        pdf.section_header("AGENT ANALYSIS SUMMARY", level=1)
        
        if agent_results:
            for result in agent_results:
                agent_type = result.get("agent_type", "Agent").replace("_", " ").title()
                confidence = result.get("confidence", 0)
                reasoning = result.get("reasoning", "No analysis available.")
                
                pdf.section_header(agent_type, level=2)
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 5, f"Confidence: {round(confidence * 100)}%", new_x="LMARGIN", new_y="NEXT")
                
                # Truncate reasoning to fit
                reasoning_clean = _clean_text(reasoning)[:800]
                pdf.body_text(reasoning_clean)
                pdf.ln(5)
        else:
            pdf.body_text("No agent analysis available.")

        # ─────────────────────────────────────────────
        #  Discussion Summary Section
        # ─────────────────────────────────────────────
        pdf.add_page()
        pdf.section_header("DISCUSSION SUMMARY", level=1)
        
        if discussion_summary:
            # Key discussion points
            if "key_points" in discussion_summary:
                pdf.section_header("Key Discussion Points", level=2)
                for point in discussion_summary["key_points"]:
                    pdf.bullet_item(point)
            
            # Questions raised
            if "questions" in discussion_summary:
                pdf.section_header("Questions Raised", level=2)
                for q in discussion_summary["questions"]:
                    pdf.bullet_item(q)
            
            # Concerns raised
            if "concerns" in discussion_summary:
                pdf.section_header("Concerns & Risks Discussed", level=2)
                for concern in discussion_summary["concerns"]:
                    pdf.bullet_item(concern)
        else:
            pdf.body_text("No discussion summary available.")

        # ─────────────────────────────────────────────
        #  Decisions Section
        # ─────────────────────────────────────────────
        pdf.add_page()
        pdf.section_header("DECISIONS MADE", level=1)
        
        if decisions:
            # Table header
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(0, 51, 102)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(100, 6, "Decision", 1, 0, "L", 1)
            pdf.cell(50, 6, "Made By", 1, 0, "L", 1)
            pdf.cell(40, 6, "Outcome", 1, 1, "L", 1)
            
            # Table rows
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 30, 30)
            for decision in decisions:
                decision_text = decision.get("description", "N/A")
                made_by = decision.get("made_by", "Committee")
                outcome = decision.get("outcome", "Approved")
                
                pdf.cell(100, 6, _clean_text(decision_text)[:60], 1, 0, "L")
                pdf.cell(50, 6, _clean_text(made_by)[:30], 1, 0, "L")
                pdf.cell(40, 6, _clean_text(outcome)[:25], 1, 1, "L")
        else:
            pdf.body_text("No formal decisions recorded.")

        # ─────────────────────────────────────────────
        #  Action Items Section
        # ─────────────────────────────────────────────
        pdf.add_page()
        pdf.section_header("ACTION ITEMS", level=1)
        
        if action_items:
            # Table header
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(0, 51, 102)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(90, 6, "Action Item", 1, 0, "L", 1)
            pdf.cell(40, 6, "Owner", 1, 0, "L", 1)
            pdf.cell(30, 6, "Due Date", 1, 0, "L", 1)
            pdf.cell(30, 6, "Status", 1, 1, "L", 1)
            
            # Table rows
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 30, 30)
            for item in action_items:
                action = item.get("description", "N/A")
                owner = item.get("owner", "TBD")
                due_date = item.get("due_date", "TBD")
                status = item.get("status", "Pending")
                
                pdf.cell(90, 6, _clean_text(action)[:55], 1, 0, "L")
                pdf.cell(40, 6, _clean_text(owner)[:25], 1, 0, "L")
                pdf.cell(30, 6, _clean_text(due_date)[:18], 1, 0, "L")
                pdf.cell(30, 6, _clean_text(status)[:18], 1, 1, "L")
        else:
            pdf.body_text("No action items recorded.")

        # ─────────────────────────────────────────────
        #  Next Steps Section
        # ─────────────────────────────────────────────
        pdf.add_page()
        pdf.section_header("NEXT STEPS", level=1)
        
        # Extract next steps from various sources
        next_steps = []
        
        # From action items
        if action_items:
            for item in action_items[:3]:
                next_steps.append(f"• Follow up on: {item.get('description', 'N/A')}")
        
        # From deal recommendation
        recommendation = deal.get("final_recommendation", "")
        if recommendation:
            next_steps.append(f"• Finalize: {recommendation}")
        
        # From analyst data
        if discussion_summary and "next_steps" in discussion_summary:
            for step in discussion_summary["next_steps"]:
                next_steps.append(f"• {step}")
        
        if next_steps:
            for step in next_steps[:6]:
                pdf.bullet_item(step)
        else:
            pdf.body_text("No next steps identified.")

        # ─────────────────────────────────────────────
        #  Appendix - Raw Agent Outputs
        # ─────────────────────────────────────────────
        if agent_results and len(agent_results) > 0:
            pdf.add_page()
            pdf.section_header("APPENDIX: DETAILED AGENT OUTPUTS", level=1)
            
            for idx, result in enumerate(agent_results, 1):
                agent_type = result.get("agent_type", "Agent")
                reasoning = _clean_text(result.get("reasoning", ""))
                data = result.get("data", {})
                
                pdf.section_header(f"{idx}. {agent_type.replace('_', ' ').title()}", level=2)
                
                if data:
                    pdf.set_font("Helvetica", "", 9)
                    for key, value in list(data.items())[:10]:  # Limit to first 10 key-value pairs
                        pdf.key_value(f"{key}:", str(value)[:80], bold_key=True)
                    pdf.ln(5)
                
                if reasoning:
                    pdf.body_text(reasoning[:500])
                pdf.ln(10)

        # Save to bytes
        buf = io.BytesIO()
        pdf.output(buf)
        buf.seek(0)
        return buf.read()

    except ImportError:
        logger.warning("fpdf not installed, falling back to markdown")
        return _generate_meeting_memo_markdown(
            deal, meeting_info, agent_results, discussion_summary, decisions, action_items
        )
    except Exception as e:
        logger.error("Meeting memo generation failed", error=str(e))
        raise


def _generate_meeting_memo_markdown(
    deal: Dict,
    meeting_info: Dict,
    agent_results: List[Dict],
    discussion_summary: Optional[Dict],
    decisions: Optional[List[Dict]],
    action_items: Optional[List[Dict]],
) -> bytes:
    """Fallback: Generate meeting memo as Markdown."""
    
    lines = [
        f"# Meeting Memo: {meeting_info.get('meeting_type', 'Deal Review')}",
        f"**Deal:** {deal.get('name', 'Untitled')}",
        f"**Date:** {meeting_info.get('date', datetime.now().strftime('%Y-%m-%d'))}",
        f"**Location:** {meeting_info.get('location', 'Virtual')}",
        "",
        "---",
        "",
    ]
    
    # Attendees
    lines.append("## ATTENDEES")
    for attendee in meeting_info.get("attendees", []):
        lines.append(f"- **{attendee.get('name', 'Unknown')}** - {attendee.get('role', '')}")
    lines.append("")
    
    # Agenda
    lines.append("## AGENDA")
    for idx, item in enumerate(meeting_info.get("agenda", []), 1):
        lines.append(f"{idx}. {item}")
    lines.append("")
    
    # Discussion Summary
    if discussion_summary:
        lines.append("## DISCUSSION SUMMARY")
        if "key_points" in discussion_summary:
            for point in discussion_summary["key_points"]:
                lines.append(f"- {point}")
        lines.append("")
    
    # Decisions
    if decisions:
        lines.append("## DECISIONS")
        for d in decisions:
            lines.append(f"- **{d.get('description', '')}** - {d.get('outcome', '')}")
        lines.append("")
    
    # Action Items
    if action_items:
        lines.append("## ACTION ITEMS")
        for item in action_items:
            lines.append(f"- [ ] {item.get('description', '')} | {item.get('owner', '')} | {item.get('due_date', '')}")
        lines.append("")
    
    return "\n".join(lines).encode("utf-8")


# ───────────────────────────────────────────────
#  Meeting Memo Generator (PPTX)
# ───────────────────────────────────────────────


def generate_meeting_memo_pptx(
    deal: Dict,
    meeting_info: Dict,
    agent_results: List[Dict],
    decisions: Optional[List[Dict]] = None,
    action_items: Optional[List[Dict]] = None,
) -> bytes:
    """
    Generate a Meeting Memo presentation (PPTX format).
    """
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        PRIMARY = RGBColor(0, 51, 102)
        SECONDARY = RGBColor(0, 112, 192)
        WHITE = RGBColor(255, 255, 255)
        BLACK = RGBColor(0, 0, 0)
        GRAY = RGBColor(128, 128, 128)

        def add_title_bar(slide, title: str, subtitle: str = ""):
            shape = slide.shapes.add_shape(
                1, Inches(0), Inches(0), prs.slide_width, Inches(1.2)
            )
            shape.fill.solid()
            shape.fill.fore_color.rgb = PRIMARY
            shape.line.fill.background()

            tf = shape.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = title
            p.font.size = Pt(28)
            p.font.color.rgb = WHITE
            p.font.bold = True

            if subtitle:
                p2 = tf.add_paragraph()
                p2.text = subtitle
                p2.font.size = Pt(14)
                p2.font.color.rgb = RGBColor(180, 198, 220)

        def add_text_box(slide, left, top, width, height, text: str, font_size=12, bold=False, color=BLACK):
            txBox = slide.shapes.add_textbox(left, top, width, height)
            tf = txBox.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = text
            p.font.size = Pt(font_size)
            p.font.bold = bold
            p.font.color.rgb = color

        # ─── Slide 1: Title
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        shape = slide.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width, prs.slide_height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = PRIMARY
        shape.line.fill.background()

        add_text_box(slide, Inches(1), Inches(2.5), Inches(11), Inches(1.5),
                     f"Meeting Memo: {meeting_info.get('meeting_type', 'Deal Review')}",
                     font_size=32, bold=True, color=WHITE)
        add_text_box(slide, Inches(1), Inches(4), Inches(11), Inches(1),
                     deal.get("name", "Deal Analysis"),
                     font_size=18, color=RGBColor(180, 198, 220))
        add_text_box(slide, Inches(1), Inches(5.5), Inches(11), Inches(0.5),
                     meeting_info.get("date", datetime.now().strftime("%Y-%m-%d")),
                     font_size=14, color=GRAY)

        # ─── Slide 2: Attendees
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_title_bar(slide, "Attendees", meeting_info.get("location", ""))

        y_offset = 1.5
        for attendee in meeting_info.get("attendees", [])[:8]:
            name = attendee.get("name", "Unknown")
            role = attendee.get("role", "")
            add_text_box(slide, Inches(0.5), Inches(y_offset), Inches(12), Inches(0.4),
                         f"• {name}" + (f" - {role}" if role else ""),
                         font_size=14, bold=True, color=SECONDARY)
            y_offset += 0.5

        # ─── Slide 3: Agenda
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_title_bar(slide, "Agenda", "")

        y_offset = 1.5
        for idx, item in enumerate(meeting_info.get("agenda", [])[:8], 1):
            add_text_box(slide, Inches(0.5), Inches(y_offset), Inches(12), Inches(0.4),
                         f"{idx}. {item}",
                         font_size=14)
            y_offset += 0.5

        # ─── Slide 4: Key Decisions
        if decisions:
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            add_title_bar(slide, "Key Decisions", "")

            y_offset = 1.5
            for d in decisions[:5]:
                decision = d.get("description", "")
                outcome = d.get("outcome", "")
                add_text_box(slide, Inches(0.5), Inches(y_offset), Inches(12), Inches(0.4),
                             f"✓ {decision}",
                             font_size=14, bold=True, color=SECONDARY)
                if outcome:
                    add_text_box(slide, Inches(0.8), Inches(y_offset + 0.4), Inches(11), Inches(0.3),
                                 f"   {outcome}",
                                 font_size=11, color=GRAY)
                y_offset += 1.0

        # ─── Slide 5: Action Items
        if action_items:
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            add_title_bar(slide, "Action Items", "")

            y_offset = 1.5
            for item in action_items[:6]:
                desc = item.get("description", "")
                owner = item.get("owner", "")
                due = item.get("due_date", "")
                
                add_text_box(slide, Inches(0.5), Inches(y_offset), Inches(12), Inches(0.3),
                             f"→ {desc}",
                             font_size=12, bold=True, color=BLACK)
                add_text_box(slide, Inches(0.8), Inches(y_offset + 0.35), Inches(11), Inches(0.3),
                             f"   Owner: {owner} | Due: {due}",
                             font_size=10, color=GRAY)
                y_offset += 0.9

        # ─── Slide 6: Agent Analysis Summary
        if agent_results:
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            add_title_bar(slide, "Agent Analysis Summary", "")

            y_offset = 1.5
            for result in agent_results[:4]:
                agent = result.get("agent_type", "Agent").replace("_", " ").title()
                conf = result.get("confidence", 0)
                
                add_text_box(slide, Inches(0.5), Inches(y_offset), Inches(12), Inches(0.3),
                             f"• {agent}: {round(conf * 100)}% confidence",
                             font_size=13, bold=True, color=SECONDARY)
                y_offset += 0.5

        # Save
        buf = io.BytesIO()
        prs.save(buf)
        buf.seek(0)
        return buf.read()

    except ImportError:
        logger.warning("python-pptx not installed for meeting memo")
        return _generate_meeting_memo_markdown(
            deal, meeting_info, agent_results, {}, decisions, action_items
        )
    except Exception as e:
        logger.error("Meeting memo PPTX generation failed", error=str(e))
        raise


# ───────────────────────────────────────────────
#  Meeting Memo Generator (HTML - for web preview)
# ───────────────────────────────────────────────


def generate_meeting_memo_html(
    deal: Dict,
    meeting_info: Dict,
    agent_results: List[Dict],
    discussion_summary: Optional[Dict] = None,
    decisions: Optional[List[Dict]] = None,
    action_items: Optional[List[Dict]] = None,
) -> bytes:
    """Generate Meeting Memo as HTML for web viewing."""
    
    html = [
        "<!DOCTYPE html>",
        "<html><head>",
        "<title>Meeting Memo</title>",
        "<style>",
        "body { font-family: 'Segoe UI', Arial, sans-serif; background: #f5f7fa; padding: 20px; }",
        ".container { max-width: 900px; margin: auto; background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }",
        "h1 { color: #003366; border-bottom: 3px solid #0072ce; padding-bottom: 10px; }",
        "h2 { color: #0072ce; margin-top: 30px; border-bottom: 1px solid #e0e0e0; padding-bottom: 5px; }",
        "table { width: 100%; border-collapse: collapse; margin: 15px 0; }",
        "th { background: #003366; color: white; padding: 10px; text-align: left; }",
        "td { padding: 10px; border-bottom: 1px solid #e0e0e0; }",
        ".badge { display: inline-block; padding: 3px 8px; border-radius: 4px; background: #e0e0e0; font-size: 12px; }",
        ".decision { background: #e8f5e9; border-left: 4px solid #4caf50; padding: 10px; margin: 5px 0; }",
        ".action { background: #fff3e0; border-left: 4px solid #ff9800; padding: 10px; margin: 5px 0; }",
        ".meta { color: #666; font-size: 14px; }",
        "</style></head><body>",
        "<div class='container'>",
        f"<h1>Meeting Memo: {meeting_info.get('meeting_type', 'Deal Review')}</h1>",
        f"<p class='meta'>Deal: <strong>{deal.get('name', 'Untitled')}</strong> | Date: {meeting_info.get('date', '')} | Location: {meeting_info.get('location', 'Virtual')}</p>",
    ]
    
    # Attendees
    html.append("<h2>Attendees</h2><ul>")
    for attendee in meeting_info.get("attendees", []):
        html.append(f"<li><strong>{attendee.get('name', '')}</strong> - {attendee.get('role', '')}</li>")
    html.append("</ul>")
    
    # Agenda
    html.append("<h2>Agenda</h2><ol>")
    for item in meeting_info.get("agenda", []):
        html.append(f"<li>{item}</li>")
    html.append("</ol>")
    
    # Decisions
    if decisions:
        html.append("<h2>Decisions</h2>")
        for d in decisions:
            html.append(f"<div class='decision'><strong>{d.get('description', '')}</strong><br><span class='meta'>Outcome: {d.get('outcome', '')}</span></div>")
    
    # Action Items
    if action_items:
        html.append("<h2>Action Items</h2><table><tr><th>Item</th><th>Owner</th><th>Due</th><th>Status</th></tr>")
        for item in action_items:
            html.append(f"<tr><td>{item.get('description', '')}</td><td>{item.get('owner', '')}</td><td>{item.get('due_date', '')}</td><td>{item.get('status', '')}</td></tr>")
        html.append("</table>")
    
    # Agent Analysis
    if agent_results:
        html.append("<h2>Agent Analysis Summary</h2>")
        for result in agent_results:
            agent = result.get("agent_type", "").replace("_", " ").title()
            conf = result.get("confidence", 0)
            html.append(f"<p><strong>{agent}</strong> <span class='badge'>{round(conf * 100)}%</span></p>")
    
    html.append("</div></body></html>")
    return "".join(html).encode("utf-8")


# ───────────────────────────────────────────────
#  Meeting Memo Generator (DOCX)
# ───────────────────────────────────────────────


def generate_meeting_memo_docx(
    deal: Dict,
    meeting_info: Dict,
    agent_results: List[Dict],
    discussion_summary: Optional[Dict] = None,
    decisions: Optional[List[Dict]] = None,
    action_items: Optional[List[Dict]] = None,
) -> bytes:
    """Generate professional Meeting Memo in DOCX format."""
    try:
        from docx import Document
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        doc = Document()

        # Styles
        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)

        # ─── Cover Page ───
        # Confidential tag
        p_conf = doc.add_paragraph()
        p_conf.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_conf = p_conf.add_run("CONFIDENTIAL")
        run_conf.font.size = Pt(28)
        run_conf.font.bold = True
        run_conf.font.color.rgb = RGBColor(0xC0, 0xC0, 0xC0)

        for _ in range(3):
            doc.add_paragraph()

        # Title
        meeting_type = meeting_info.get("meeting_type", "Deal Review")
        p_title = doc.add_paragraph()
        p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_title = p_title.add_run(f"Meeting Memo: {meeting_type}")
        run_title.font.size = Pt(26)
        run_title.font.bold = True
        run_title.font.color.rgb = RGBColor(0x00, 0x33, 0x66)

        # Deal Name
        p_deal = doc.add_paragraph()
        p_deal.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_deal = p_deal.add_run(deal.get("name", "Untitled Deal"))
        run_deal.font.size = Pt(18)
        run_deal.font.color.rgb = RGBColor(0x50, 0x50, 0x50)

        doc.add_paragraph()

        # Metadata table
        meta_table = doc.add_table(rows=3, cols=2)
        meta_table.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        meta_data = [
            ("Date", meeting_info.get("date", datetime.now().strftime("%Y-%m-%d"))),
            ("Location", meeting_info.get("location", "Virtual")),
            ("Duration", meeting_info.get("duration", "60 min")),
        ]
        
        for i, (label, val) in enumerate(meta_data):
            meta_table.rows[i].cells[0].text = label
            meta_table.rows[i].cells[1].text = val
            meta_table.rows[i].cells[0].paragraphs[0].runs[0].font.bold = True

        doc.add_page_break()

        # ─── Attendees ───
        doc.add_heading("Attendees", level=1)
        attendees = meeting_info.get("attendees", [])
        if attendees:
            for att in attendees:
                p = doc.add_paragraph(style="List Bullet")
                run = p.add_run(f"{att.get('name', 'Unknown')}")
                run.font.bold = True
                if att.get("role") or att.get("organization"):
                    p.add_run(f" ({att.get('role', '')} - {att.get('organization', '')})")
        else:
            doc.add_paragraph("No attendees recorded.")

        # ─── Agenda ───
        doc.add_heading("Agenda", level=1)
        agenda = meeting_info.get("agenda", [])
        if agenda:
            for item in agenda:
                doc.add_paragraph(item, style="List Number")
        else:
            doc.add_paragraph("No agenda recorded.")

        # ─── Discussion Summary ───
        doc.add_heading("Discussion Summary", level=1)
        if discussion_summary:
            if discussion_summary.get("key_points"):
                doc.add_heading("Key Points", level=2)
                for pt in discussion_summary["key_points"]:
                    doc.add_paragraph(pt, style="List Bullet")
            
            if discussion_summary.get("questions"):
                doc.add_heading("Questions Raised", level=2)
                for q in discussion_summary["questions"]:
                    doc.add_paragraph(q, style="List Bullet")
        
        # Agent results
        if agent_results:
            doc.add_heading("Agent Analysis Contributions", level=2)
            for res in agent_results:
                agent = res.get("agent_type", "").replace("_", " ").title()
                conf = round(res.get("confidence", 0) * 100)
                doc.add_paragraph(f"{agent} ({conf}% Confidence):", style="List Bullet").runs[0].font.bold = True
                doc.add_paragraph(res.get("reasoning", "")[:500] + "...")

        # ─── Outcomes ───
        doc.add_heading("Decisions & Action Items", level=1)
        
        if decisions:
            doc.add_heading("Decisions Made", level=2)
            dec_table = doc.add_table(rows=1 + len(decisions), cols=3, style="Table Grid")
            dec_table.rows[0].cells[0].text = "Decision"
            dec_table.rows[0].cells[1].text = "Made By"
            dec_table.rows[0].cells[2].text = "Outcome"
            for i, d in enumerate(decisions, 1):
                dec_table.rows[i].cells[0].text = d.get("description", "N/A")
                dec_table.rows[i].cells[1].text = d.get("made_by", "Committee")
                dec_table.rows[i].cells[2].text = d.get("outcome", "Approved")

        if action_items:
            doc.add_heading("Action Items", level=2)
            act_table = doc.add_table(rows=1 + len(action_items), cols=4, style="Table Grid")
            act_headers = ["Action Item", "Owner", "Due Date", "Status"]
            for i, h in enumerate(act_headers):
                act_table.rows[0].cells[i].text = h
            for i, item in enumerate(action_items, 1):
                act_table.rows[i].cells[0].text = item.get("description", "N/A")
                act_table.rows[i].cells[1].text = item.get("owner", "TBD")
                act_table.rows[i].cells[2].text = item.get("due_date", "TBD")
                act_table.rows[i].cells[3].text = item.get("status", "Pending")

        # Save to buffer
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf.read()

    except ImportError:
        logger.warning("python-docx not installed, falling back to markdown")
        return _generate_meeting_memo_markdown(
            deal, meeting_info, agent_results, discussion_summary, decisions, action_items
        )
    except Exception as e:
        logger.error("Meeting memo DOCX generation failed", error=str(e))
        raise


# ───────────────────────────────────────────────
#  Main entry point
# ───────────────────────────────────────────────


def generate_meeting_memo_report(
    deal: Dict,
    meeting_info: Dict,
    agent_results: List[Dict],
    format: str = "pdf",
    discussion_summary: Optional[Dict] = None,
    decisions: Optional[List[Dict]] = None,
    action_items: Optional[List[Dict]] = None,
) -> bytes:
    """
    Generate Meeting Memo in specified format.
    
    Args:
        deal: Deal metadata
        meeting_info: Meeting details (date, attendees, meeting_type, agenda)
        agent_results: Agent analysis outputs
        format: Output format - "pdf", "pptx", "html", or "docx"
        discussion_summary: Discussion points
        decisions: List of decisions
        action_items: List of action items
    
    Returns:
        bytes of the generated memo
    """
    format = format.lower()
    
    if format == "pdf":
        return generate_meeting_memo(
            deal, meeting_info, agent_results, 
            discussion_summary, decisions, action_items
        )
    elif format == "pptx":
        return generate_meeting_memo_pptx(
            deal, meeting_info, agent_results, 
            decisions, action_items
        )
    elif format == "html":
        return generate_meeting_memo_html(
            deal, meeting_info, agent_results,
            discussion_summary, decisions, action_items
        )
    elif format == "docx":
        return generate_meeting_memo_docx(
            deal, meeting_info, agent_results,
            discussion_summary, decisions, action_items
        )
    else:
        raise ValueError(f"Unsupported format: {format}. Use 'pdf', 'pptx', 'html', or 'docx'")


# ───────────────────────────────────────────────
#  Main entry point
# ───────────────────────────────────────────────


def generate_meeting_memo_report(
    deal: Dict,
    meeting_info: Dict,
    agent_results: List[Dict],
    format: str = "pdf",
    discussion_summary: Optional[Dict] = None,
    decisions: Optional[List[Dict]] = None,
    action_items: Optional[List[Dict]] = None,
) -> bytes:
    """
    Generate Meeting Memo in specified format.
    
    Args:
        deal: Deal metadata
        meeting_info: Meeting details (date, attendees, meeting_type, agenda)
        agent_results: Agent analysis outputs
        format: Output format - "pdf", "pptx", or "html"
        discussion_summary: Discussion points
        decisions: List of decisions
        action_items: List of action items
    
    Returns:
        bytes of the generated memo
    """
    format = format.lower()
    
    if format == "pdf":
        return generate_meeting_memo(
            deal, meeting_info, agent_results, 
            discussion_summary, decisions, action_items
        )
    elif format == "pptx":
        return generate_meeting_memo_pptx(
            deal, meeting_info, agent_results, 
            decisions, action_items
        )
    elif format == "html":
        return generate_meeting_memo_html(
            deal, meeting_info, agent_results,
            discussion_summary, decisions, action_items
        )
    else:
        raise ValueError(f"Unsupported format: {format}. Use 'pdf', 'pptx', or 'html'")