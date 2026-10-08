"""
McKinsey-Style Report Generator
Generates PPTX, Excel, and PDF deliverables from deal analysis data.
"""

import io
import json
import re
from typing import Dict, Any, Optional, List
from datetime import datetime
import structlog
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from app.core.reports.branding import load_custom_branding, apply_branding_to_reportlab
from app.core.reports.infographic_engine import InfographicEngine

logger = structlog.get_logger()


def _safe_get(data: Dict, *keys, default="N/A"):
    """Safely traverse nested dict keys"""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current if current is not None else default


def _clean_text(text: Any) -> str:
    """Sanitize text for PDF/PPTX (handle smart quotes, non-ASCII chars)."""
    if text is None:
        return ""
    s = str(text)
    # Replace common Unicode "smart" characters with ASCII equivalents
    replacements = {
        "\u2013": "-",  # en dash
        "\u2014": "--",  # em dash
        "\u2018": "'",  # left single quote
        "\u2019": "'",  # right single quote
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2022": "*",  # bullet
        "\u2026": "...",  # ellipsis
        "\u2192": "->",  # arrow
    }
    for k, v in replacements.items():
        s = s.replace(k, v)

    # Final fallback: strip non-printable/non-latin characters that crash reportlab
    return "".join(c for c in s if ord(c) < 128 or c.isprintable())


def _safe_content(text: Any, fallback: str = "") -> str:
    """Return fallback if text is an LLM error placeholder; otherwise clean it."""
    s = _clean_text(text)
    if not s:
        return fallback
    error_prefixes = ("[Error]", "[Rate limited]", "[Error:", "Error:")
    if any(s.startswith(p) for p in error_prefixes):
        return fallback
    return s


def _confidence_label(result: Dict) -> str:
    """Only render confidence percentages when calibration is explicitly attested."""
    data = result.get("data", {}) if isinstance(result, dict) else {}
    confidence = result.get("confidence") if isinstance(result, dict) else None
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return "Not reported"
    if not isinstance(data, dict) or not (
        data.get("confidence_calibrated") is True
        or data.get("confidence_basis") in {"calibrated", "validated_calibration"}
    ):
        return "Not calibrated"
    return f"{round(confidence * 100)}%"


def _explicit_financial_metrics(agent_results: List[Dict]) -> List[tuple[str, Any]]:
    """Flatten only scalar metrics present in the financial agent's recorded output."""
    result = next((r for r in reversed(agent_results) if r.get("agent_type") == "financial_analyst"), {})
    data = result.get("data", {}) if isinstance(result, dict) else {}
    excluded = {"confidence", "score", "status", "provider", "recommendation", "final_score"}
    metric_terms = ("revenue", "arr", "mrr", "ebitda", "margin", "growth", "cash", "burn", "runway", "debt", "valuation", "wacc", "cac", "nrr", "grr", "churn", "ltv", "payback", "gross_profit", "enterprise_value", "equity_value")
    metrics: List[tuple[str, Any]] = []

    def visit(value: Any, prefix: str = "") -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            label = f"{prefix} / {key}" if prefix else str(key)
            if isinstance(item, dict):
                visit(item, label)
            elif isinstance(item, (str, int, float)) and not isinstance(item, bool) and item not in ("", "N/A", "unknown"):
                key_lower = str(key).lower()
                if key_lower not in excluded and any(term in key_lower for term in metric_terms):
                    metrics.append((label, item))

    if isinstance(data, dict):
        visit(data)
    return metrics[:40]


def _curated_financial_points(analyst_data: Dict) -> List[Dict[str, Any]]:
    evidence = analyst_data.get("_evidence_brief", {}) if isinstance(analyst_data, dict) else {}
    points = evidence.get("data_points", []) if isinstance(evidence, dict) else []
    return [point for point in points if isinstance(point, dict) and point.get("value") is not None]


def _prioritize_financial_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Put latest-period decision metrics first while preserving remaining evidence order."""
    if not points:
        return []
    years = [
        int(match.group())
        for point in points
        if (match := re.search(r"\d{4}", str(point.get("period") or "")))
    ]
    latest_year = max(years) if years else None
    latest = [
        point for point in points
        if latest_year is not None
        and (match := re.search(r"\d{4}", str(point.get("period") or "")))
        and int(match.group()) == latest_year
    ]
    priority = (
        "revenue", "operating_margin_percent", "operating_income", "free_cash_flow",
        "operating_cash_flow", "long_term_debt", "cash", "net_income",
    )
    rank = {metric: index for index, metric in enumerate(priority)}
    latest.sort(key=lambda point: rank.get(str(point.get("metric")), len(rank)))
    latest_ids = {id(point) for point in latest}
    return latest + [point for point in points if id(point) not in latest_ids]


# ───────────────────────────────────────────────
#  1. PowerPoint Report (Executive Summary)
# ───────────────────────────────────────────────


def generate_pptx(
    deal: Dict,
    analyst_data: Dict,
    agent_results: List[Dict],
    provenance_records: Optional[List[Dict]] = None,
    deal_stage: str = "deep_dive",
) -> bytes:
    """
    Generate McKinsey-style PPTX with:
    - Title slide
    - Executive Summary
    - Key Financial Metrics
    - Market Landscape
    - Risk Assessment
    - Recommendation
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Color palette (McKinsey blue)
    PRIMARY = RGBColor(0, 51, 102)  # Dark navy
    SECONDARY = RGBColor(0, 112, 192)  # Blue
    ACCENT = RGBColor(0, 176, 80)  # Green
    ACCENT_RED = RGBColor(192, 0, 0)  # Red for risks
    LIGHT_BG = RGBColor(242, 242, 242)  # Light gray
    WHITE = RGBColor(255, 255, 255)
    BLACK = RGBColor(0, 0, 0)

    def add_title_bar(slide, title: str, subtitle: str = ""):
        """Add McKinsey-style title bar to slide"""
        # Dark bar at top
        from pptx.util import Inches, Pt

        shape = slide.shapes.add_shape(
            1, Inches(0), Inches(0), prs.slide_width, Inches(1.2)  # 1 = rectangle
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

    def add_text_box(
        slide,
        left,
        top,
        width,
        height,
        text: str,
        font_size=12,
        bold=False,
        color=BLACK,
    ):
        txBox = slide.shapes.add_textbox(left, top, width, height)
        tf = txBox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = text
        p.font.size = Pt(font_size)
        p.font.bold = bold
        p.font.color.rgb = color

    # ─── Slide 1: Title slide ───
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    shape = slide.shapes.add_shape(
        1, Inches(0), Inches(0), prs.slide_width, prs.slide_height
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = PRIMARY
    shape.line.fill.background()

    add_text_box(
        slide,
        Inches(1),
        Inches(2),
        Inches(11),
        Inches(1.5),
        deal.get("name", "Deal Analysis"),
        font_size=36,
        bold=True,
        color=WHITE,
    )
    add_text_box(
        slide,
        Inches(1),
        Inches(3.5),
        Inches(11),
        Inches(1),
        f"M&A Due Diligence Report — {deal.get('target_company', 'Target Co.')}",
        font_size=18,
        color=RGBColor(180, 198, 220),
    )
    add_text_box(
        slide,
        Inches(1),
        Inches(5),
        Inches(11),
        Inches(0.5),
        f"Prepared by DealForge AI | {datetime.now().strftime('%B %d, %Y')}",
        font_size=14,
        color=RGBColor(150, 170, 200),
    )
    add_text_box(
        slide,
        Inches(1),
        Inches(5.5),
        Inches(11),
        Inches(0.5),
        "CONFIDENTIAL",
        font_size=12,
        bold=True,
        color=RGBColor(200, 200, 200),
    )

    # ─── Slide 2: Executive Summary (from Business Analyst) ───
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "Executive Summary", f"Target: {deal.get('target_company', 'Unknown')}")

    exec_sum = analyst_data.get("executive_summary", {})
    score = deal.get("final_score")
    if exec_sum:
        summary_lines = [
            f"SITUATION:",
            _safe_content(exec_sum.get('situation', ''), 'Analysis pending'),
            f"",
            f"COMPLICATION:",
            _safe_content(exec_sum.get('complication', ''), 'Analysis pending'),
            f"",
            f"QUESTION:",
            _safe_content(exec_sum.get('question', ''), 'Analysis pending'),
            f"",
            f"AI SYNTHESIS (NOT A RECORDED DECISION):",
            _safe_content(exec_sum.get('answer', ''), 'Analysis pending'),
        ]
    else:
        score = deal.get("final_score")
        score_text = f"{round(score * 100)}%" if score is not None else "Pending"
        status = deal.get("status", "created")

        summary_lines = [
            f"Target Company: {deal.get('target_company', 'N/A')}",
            f"Industry: {deal.get('industry', 'N/A').replace('_', ' ').title()}",
            f"Deal Score: {score_text}",
            f"Status: {status.title()}",
            f"Agents Deployed: {len(deal.get('agents_run', []))}",
            f"Created: {deal.get('created_at', 'N/A')[:19]}",
        ]

    add_text_box(
        slide,
        Inches(0.5),
        Inches(1.5),
        Inches(12),
        Inches(5.5),
        "\n".join(summary_lines),
        font_size=16,
    )

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "Recorded Financial Metrics & Evidence", f"Target: {deal.get('target_company', 'N/A')}")
    recorded_metrics = _prioritize_financial_points(_curated_financial_points(analyst_data))
    metrics_text = "\n".join(
        f"{point.get('metric', 'Metric').replace('_', ' ').title()}: "
        f"{_report_percent(point['value']) if str(point.get('metric', '')).endswith('_percent') else _report_currency(point['value'])}"
        f" | {point.get('period') or 'Period not established'}"
        f" | {point.get('basis', 'unverified')}"
        f" | {point.get('source_id') or 'No citation'}"
        for point in recorded_metrics[:14]
    )
    add_text_box(
        slide, Inches(0.8), Inches(1.6), Inches(11.8), Inches(4.8),
        metrics_text or "No structured financial data points were recorded. Forecasts are omitted rather than estimated.",
        font_size=14,
    )
    add_text_box(
        slide, Inches(0.8), Inches(6.5), Inches(11.8), Inches(0.5),
        "Recorded values are not verified unless a source citation is shown.",
        font_size=10, color=SECONDARY,
    )

    # ─── Slide 3b: Infographic Financials (Waterfall) ───
    try:
        from app.core.reports.infographic_engine import InfographicEngine
        fact_base = deal.get("fact_base", {})
        metrics_fb = fact_base.get("metrics", {})
        if metrics_fb.get("historical_revenue"):
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            add_title_bar(slide, "Revenue Bridge & Growth Trajectory", f"Target: {deal.get('target_company', 'N/A')}")
            
            rev_data = metrics_fb.get("historical_revenue", [])
            labels_wf = [r.get("year", f"Y{i}") for i, r in enumerate(rev_data)]
            values_wf = [r.get("amount", 0) for i, r in enumerate(rev_data)]
            
            chart_bytes = InfographicEngine.revenue_waterfall(labels_wf, values_wf, "Revenue Trajectory")
            slide.shapes.add_picture(io.BytesIO(chart_bytes), Inches(2), Inches(1.5), Inches(9), Inches(5))
    except Exception as e:
        logger.error("pptx_waterfall_error", error=str(e))

    # ─── Slide 3c: Valuation Summary (Football Field) ───
    valuations = deal.get("valuation_output", {}).get("valuations", []) or deal.get("financial_output", {}).get("valuations", [])
    if valuations:
        try:
            from app.core.reports.infographic_engine import InfographicEngine
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            add_title_bar(slide, "Valuation Summary (Football Field)", f"Implied EV Range")
            
            if isinstance(valuations, dict):
                valuations = [{"method": k, **v} for k, v in valuations.items() if isinstance(v, dict)]
            
            chart_bytes = InfographicEngine.football_field_chart(
                valuations=valuations,
                title="Valuation Range Analysis",
                current_price=deal.get("current_price")
            )
            slide.shapes.add_picture(io.BytesIO(chart_bytes), Inches(2), Inches(1.5), Inches(9), Inches(5))
        except Exception as e:
            logger.error("pptx_football_field_error", error=str(e))

    # ─── Slide 4: Risk Matrix ───
    risk_matrix = []
    for r in agent_results:
        if r.get("agent_type") == "risk_assessor":
            risk_matrix = r.get("data", {}).get("risks", [])
            break
    
    if risk_matrix:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_title_bar(slide, "Risk Assessment & Evidence", "Critical Deal Blocker Audit")
        
        y_offset = 1.5
        for risk in risk_matrix[:4]: # Top 4
            r_title = risk.get("risk", "Unknown Risk")
            r_sev = _severity_label(risk.get("severity", "Medium"))
            r_mit = risk.get("mitigation") or "N/A"
            r_evid = risk.get("evidence") or "Analysis pending further diligence."

            color = ACCENT_RED if r_sev.lower() in ("high", "critical") else SECONDARY
            
            add_text_box(slide, Inches(0.5), Inches(y_offset), Inches(12), Inches(0.4), f"● {r_title} ({r_sev})", font_size=14, bold=True, color=color)
            add_text_box(slide, Inches(0.8), Inches(y_offset + 0.35), Inches(5.5), Inches(0.8), f"Mitigation: {r_mit}", font_size=11, color=BLACK)
            add_text_box(slide, Inches(6.5), Inches(y_offset + 0.35), Inches(6.5), Inches(0.8), f"Evidence: {r_evid}", font_size=11, color=RGBColor(80, 80, 80))
            y_offset += 1.3

    # ─── Slide 5: Takeaways ───
    takeaways = analyst_data.get("key_takeaways", [])
    if takeaways:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_title_bar(
            slide, "Key Takeaways & Strategic Fit", f"Target: {deal.get('target_company', 'Unknown')}"
        )
        y_offset = 1.5
        for tk in takeaways[:4]:  # Max 4 to fit slide
            title = tk.get("title", "")
            desc = tk.get("description", "")

            # Draw bullet block
            add_text_box(
                slide,
                Inches(0.5),
                Inches(y_offset),
                Inches(12),
                Inches(0.4),
                f"■ {title}",
                font_size=16,
                bold=True,
                color=SECONDARY,
            )
            add_text_box(
                slide,
                Inches(0.8),
                Inches(y_offset + 0.4),
                Inches(11.5),
                Inches(0.8),
                desc,
                font_size=14,
                color=BLACK,
            )
            y_offset += 1.4
    else:
        # Fallback 2-Column Layout
        for result in agent_results:
            agent_type = result.get("agent_type", "Agent")
            if agent_type == "financial_analyst":
                continue  # already handled above

            label = agent_type.replace("_", " ").title()
            reasoning = result.get("reasoning", "No analysis data available.")
            confidence_label = _confidence_label(result)
            provider = result.get("provider", "unknown")

            slide = prs.slides.add_slide(prs.slide_layouts[6])
            add_title_bar(
                slide,
                label,
                f"Confidence: {confidence_label} | Provider: {provider}",
            )

            display_text = reasoning[:1200] + ("..." if len(reasoning) > 1200 else "")
            paragraphs = [p for p in display_text.split("\n") if p.strip()]

            left_text = "\n\n".join(paragraphs[: len(paragraphs) // 2 + 1])
            right_text = "\n\n".join(paragraphs[len(paragraphs) // 2 + 1 :])

            add_text_box(
                slide,
                Inches(0.5),
                Inches(1.5),
                Inches(6),
                Inches(5),
                left_text,
                font_size=12,
            )
            sep = slide.shapes.add_shape(
                1, Inches(6.66), Inches(1.8), Inches(0.02), Inches(4.5)
            )
            sep.fill.solid()
            sep.fill.fore_color.rgb = RGBColor(200, 200, 200)
            sep.line.fill.background()
            add_text_box(
                slide,
                Inches(6.8),
                Inches(1.5),
                Inches(6),
                Inches(5),
                right_text,
                font_size=12,
            )

    # ─── Final Slide: Recommendation ───
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title_bar(slide, "Final Recommendation & Next Steps")

    action_items = analyst_data.get("action_items", [])
    if action_items:
        rec_text = "\n\n".join(action_items)
        add_text_box(
            slide,
            Inches(1),
            Inches(2),
            Inches(11),
            Inches(4),
            rec_text,
            font_size=16,
            bold=False,
            color=BLACK,
        )
    else:
        rec = deal.get("final_recommendation") or "No recommendation recorded; human review required."
        rec_color = ACCENT if deal.get("final_recommendation") else SECONDARY

        add_text_box(
            slide,
            Inches(1),
            Inches(2.0),
            Inches(11),
            Inches(1),
            "Strategic Verdict:",
            font_size=18,
            bold=True,
            color=PRIMARY,
        )
        add_text_box(
            slide,
            Inches(1),
            Inches(2.5),
            Inches(11),
            Inches(1),
            rec,
            font_size=24,
            bold=True,
            color=rec_color,
        )

        # Score Waterfall in PPTX
        scoring_data = deal.get("scoring_output", {})
        if scoring_data and scoring_data.get("components"):
            try:
                from app.core.reports.infographic_engine import InfographicEngine
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                add_title_bar(slide, "Deal Score Analysis (Quantitative Bridge)", "Score Build-up & Risk Impact")
                
                comp_list = scoring_data.get("components", [])
                labels_sw = [c.get("name") for c in comp_list]
                values_sw = [c.get("score") for c in comp_list]
                
                labels_sw.append("Final Score")
                values_sw.append(scoring_data.get("total_score", sum(values_sw)))
                
                chart_bytes = InfographicEngine.revenue_waterfall(
                    labels=labels_sw,
                    values=values_sw,
                    title="Deal Score Composition"
                )
                slide.shapes.add_picture(io.BytesIO(chart_bytes), Inches(2), Inches(1.5), Inches(9), Inches(5))
            except Exception as e:
                logger.error("pptx_score_waterfall_error", error=str(e))

        add_text_box(
            slide,
            Inches(1),
            Inches(3.8),
            Inches(11),
            Inches(0.5),
            "AI-Drafted Follow-ups (verify before action):",
            font_size=16,
            bold=True,
            color=PRIMARY,
        )

        steps = analyst_data.get("action_items") or ["No analysis-specific follow-up actions were recorded."]
        add_text_box(
            slide,
            Inches(1.2),
            Inches(4.4),
            Inches(10),
            Inches(2),
            "\n\n".join(steps),
            font_size=14,
            color=BLACK,
        )

    add_text_box(
        slide,
        Inches(1),
        Inches(6.7),
        Inches(11),
        Inches(0.5),
        "This report was generated by DealForge AI Multi-Agent system incorporating McKinsey-style visualizations.",
        font_size=10,
        color=RGBColor(128, 128, 128),
    )

    # Save to bytes
    # ─── Slide 6: Data & Audit Trail ───
    if deal.get("consistency_warnings") or provenance_records:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_title_bar(
            slide, "Data & Audit Trail", "System consistency checks and tool provenance"
        )

        y_offset = 1.5
        warnings = deal.get("consistency_warnings", [])
        if warnings:
            add_text_box(
                slide,
                Inches(0.5),
                Inches(y_offset),
                Inches(11),
                Inches(0.5),
                "Consistency Warnings",
                font_size=18,
                bold=True,
                color=PRIMARY,
            )
            y_offset += 0.5
            for w in warnings[:3]:  # Top 3 warnings to fit on slide
                sev = w.get("severity", "warning").upper()
                msg = f"[{sev}] {w.get('message', '')} ({w.get('field', '')})"
                color = ACCENT_RED if sev == "MATERIAL" else RGBColor(227, 114, 34)
                add_text_box(
                    slide,
                    Inches(1),
                    Inches(y_offset),
                    Inches(11),
                    Inches(0.3),
                    msg,
                    font_size=12,
                    color=color,
                )
                y_offset += 0.3

        if provenance_records:
            add_text_box(
                slide,
                Inches(0.5),
                Inches(y_offset),
                Inches(11),
                Inches(0.5),
                "Data Integration Provenance",
                font_size=18,
                bold=True,
                color=PRIMARY,
            )
            y_offset += 0.5
            for rec in provenance_records[:6]:
                agent = rec.get("agent_name", "System")
                tool = rec.get("tool_name", "UnknownTool")
                ts = rec.get("timestamp", "").split("T")[0]
                msg = f"{agent} pulled data via {tool} on {ts}"
                add_text_box(
                    slide,
                    Inches(1),
                    Inches(y_offset),
                    Inches(11),
                    Inches(0.3),
                    msg,
                    font_size=12,
                    color=BLACK,
                )
                y_offset += 0.3

    evidence_sources = analyst_data.get("_evidence_brief", {}).get("sources", [])
    if evidence_sources:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_title_bar(slide, "Source Register", f"Target: {deal.get('target_company', 'Unknown')}")
        y_offset = 1.45
        for source in evidence_sources[:10]:
            source_id = str(source.get("id") or "Source")
            title = str(source.get("title") or source.get("name") or "Recorded filing")
            period = str(source.get("period") or "Period not recorded")
            filed = str(source.get("filed") or "Filing date not recorded")
            url = str(source.get("url") or "")
            add_text_box(
                slide, Inches(0.6), Inches(y_offset), Inches(12), Inches(0.3),
                f"[{source_id}] {title} | {period} | filed {filed}",
                font_size=11, bold=True, color=BLACK,
            )
            link_box = slide.shapes.add_textbox(Inches(0.8), Inches(y_offset + 0.3), Inches(11.8), Inches(0.28))
            link_p = link_box.text_frame.paragraphs[0]
            link_run = link_p.add_run()
            link_run.text = url or "No source URL recorded"
            link_run.font.size = Pt(8)
            link_run.font.color.rgb = SECONDARY
            if url.startswith("https://"):
                link_run.hyperlink.address = url
            y_offset += 0.86

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


# ───────────────────────────────────────────────
#  2. Excel Report (Financial Model)
# ───────────────────────────────────────────────


def generate_excel(
    deal: Dict,
    analyst_data: Dict,
    agent_results: List[Dict],
    provenance_records: Optional[List[Dict]] = None,
    deal_stage: str = "deep_dive",
) -> bytes:
    """
    Generate PE-Grade Excel workbook with 8 sheets:
    1. Executive Summary (SCQ)
    2. Income Statement
    3. DCF Analysis
    4. Comparable Companies
    5. LBO Returns
    6. Risk Matrix
    7. Agent Analysis Detail
    8. Sources & References
    """
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # ─── Styles ───
    header_font = Font(name="Calibri", bold=True, size=12, color="FFFFFF")
    header_fill = PatternFill(start_color="003366", end_color="003366", fill_type="solid")
    section_font = Font(name="Calibri", bold=True, size=11, color="003366")
    bold_font = Font(name="Calibri", bold=True, size=11)
    input_font = Font(name="Calibri", color="0000FF", size=11) # Blue for inputs
    neg_font = Font(name="Calibri", color="FF0000", size=11) # Red for negatives
    calc_font = Font(name="Calibri", color="000000", size=11) # Black for calc
    
    thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))
    
    def style_header(ws, row, cols):
        for col in range(1, cols + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

    # Extract Agent Data Helpers
    def get_agent_data(agent_type: str) -> Dict:
        for r in agent_results:
            if r.get("agent_type") == agent_type:
                return r.get("data", {})
        return {}

    fin_data = get_agent_data("financial_analyst")
    val_data = get_agent_data("valuation_agent")
    dcf_data = get_agent_data("dcf_lbo_architect")
    risk_data = get_agent_data("risk_assessor")

    # ─── Sheet 1: Executive Summary ───
    ws1 = wb.active
    ws1.title = "Executive Summary"
    ws1.column_dimensions["A"].width = 25
    ws1.column_dimensions["B"].width = 80

    ws1.merge_cells("A1:D1")
    ws1["A1"] = "DEALFORGE M&A ANALYSIS"
    ws1["A1"].font = Font(name="Calibri", bold=True, size=18, color="003366")

    score = deal.get("final_score")
    score_text = f"{round(score * 100)}%" if score is not None else "Pending"
    rec = deal.get("final_recommendation") or "No recommendation recorded; human review required"

    ws1["A3"] = "Target Company"
    ws1["B3"] = deal.get("target_company", "N/A")
    ws1["A4"] = "Industry"
    ws1["B4"] = deal.get("industry", "N/A").title()
    ws1["A5"] = "Deal Score"
    ws1["B5"] = score_text
    ws1["B5"].font = Font(bold=True, color="00B050" if score and score >= 0.75 else ("E26B0A" if score and score >= 0.5 else "C00000"))
    ws1["A6"] = "Recorded Recommendation"
    ws1["B6"] = rec
    ws1["B6"].font = bold_font
    ws1["A7"] = "Date"
    from datetime import datetime
    ws1["B7"] = datetime.now().strftime("%Y-%m-%d")

    ws1["A9"] = "Executive Summary (AI synthesis draft; verify against evidence)"
    ws1["A9"].font = section_font
    
    exec_sum = analyst_data.get("executive_summary", {})
    ws1["A10"] = "Situation"
    ws1["B10"] = exec_sum.get("situation", "N/A")
    ws1["A11"] = "Complication"
    ws1["B11"] = exec_sum.get("complication", "N/A")
    ws1["A12"] = "Question"
    ws1["B12"] = exec_sum.get("question", "N/A")
    ws1["A13"] = "Answer"
    ws1["B13"] = deal.get("final_recommendation") or "No recommendation recorded; human review required"
    for r in range(10, 14):
        ws1[f"B{r}"].alignment = Alignment(wrap_text=True)

    # ─── Sheet 2: Income Statement ───
    ws2 = wb.create_sheet("Income Statement")
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 20
    ws2.column_dimensions["C"].width = 22
    ws2.column_dimensions["D"].width = 34
    ws2.column_dimensions["E"].width = 48

    for col, title in enumerate(("Metric", "Value (USD unless %)", "Period", "Evidence basis", "Source ID"), 1):
        ws2.cell(row=1, column=col, value=title)
    style_header(ws2, 1, 5)

    curated_points = _prioritize_financial_points(_curated_financial_points(analyst_data))
    if curated_points:
        for row, point in enumerate(curated_points, 2):
            ws2.cell(row=row, column=1, value=str(point.get("metric", "Metric")).replace("_", " ").title())
            value_cell = ws2.cell(row=row, column=2, value=point.get("value"))
            if isinstance(point.get("value"), (int, float)) and not isinstance(point.get("value"), bool):
                metric_name = str(point.get("metric", ""))
                value = abs(point["value"])
                if metric_name.endswith("_percent"):
                    value_cell.number_format = '0.00"%"'
                elif value >= 1_000_000_000:
                    value_cell.number_format = '$#,##0.0,,,"bn";($#,##0.0,,,"bn");-'
                elif value >= 1_000_000:
                    value_cell.number_format = '$#,##0.0,,"m";($#,##0.0,,"m");-'
                else:
                    value_cell.number_format = '$#,##0.00;($#,##0.00);-'
            ws2.cell(row=row, column=3, value=point.get("period") or "Not established")
            ws2.cell(row=row, column=4, value=point.get("basis", "unverified"))
            ws2.cell(row=row, column=5, value=point.get("source_id") or "No citation recorded")
    else:
        ws2.cell(row=2, column=1, value="No structured financial data points recorded")

    # ─── Sheet 3: DCF Analysis ───
    ws3 = wb.create_sheet("DCF Analysis")
    ws3.column_dimensions["A"].width = 25
    ws3.column_dimensions["B"].width = 15

    ws3["A1"] = "DCF Assumptions & Valuation"
    ws3["A1"].font = section_font
    
    ws3["A3"] = "Model status"
    ws3["B3"] = "Not calculated"
    ws3["A4"] = "Required inputs"
    ws3["B4"] = "Source-backed forecast free cash flows, WACC, terminal growth, net debt, valuation date"
    ws3["B4"].alignment = Alignment(wrap_text=True)
    ws3["A6"] = "No valuation is presented until transaction-specific assumptions and source data are supplied."

    # ─── Sheet 4: Comparable Companies ───
    ws4 = wb.create_sheet("Comps")
    headers4 = ["Company", "Revenue ($M)", "EBITDA ($M)", "EV/Rev", "EV/EBITDA", "P/E"]
    for col, h in enumerate(headers4, 1):
        ws4.cell(row=1, column=col, value=h)
    style_header(ws4, 1, len(headers4))
    
    comps = val_data.get("comparables", [])
    r = 2
    for c in comps:
        ws4.cell(row=r, column=1, value=c.get("name", "Unspecified peer"))
        ws4.cell(row=r, column=2, value=c.get("revenue"))
        ws4.cell(row=r, column=3, value=c.get("ebitda"))
        ws4.cell(row=r, column=4, value=c.get("ev_rev"))
        ws4.cell(row=r, column=5, value=c.get("ev_ebitda"))
        ws4.cell(row=r, column=6, value=c.get("pe"))
        r += 1

    if not comps:
        ws4.cell(row=2, column=1, value="No sourced comparable-company data recorded")

    # ─── Sheet 5: LBO Returns ───
    ws5 = wb.create_sheet("LBO Returns")
    ws5.column_dimensions["A"].width = 25
    ws5.column_dimensions["B"].width = 15
    ws5["A1"] = "LBO Returns Analysis"
    ws5["A1"].font = section_font

    ws5["A3"] = "Model status"
    ws5["B3"] = "Not calculated"
    ws5["A4"] = "Required inputs"
    ws5["B4"] = "Entry/exit valuation, financing mix, holding period, debt paydown, and transaction fees"
    ws5["B4"].alignment = Alignment(wrap_text=True)
    ws5["A6"] = "Returns are not estimated without transaction-specific assumptions."

    # ─── Sheet 6: Risk Matrix ───
    ws6 = wb.create_sheet("Risk Matrix")
    ws6.column_dimensions["A"].width = 25
    ws6.column_dimensions["B"].width = 15
    ws6.column_dimensions["C"].width = 50
    ws6.column_dimensions["D"].width = 40

    headers6 = ["Risk", "Category", "Severity", "Mitigation"]
    for col, h in enumerate(headers6, 1):
        ws6.cell(row=1, column=col, value=h)
    style_header(ws6, 1, 4)

    risks = risk_data.get("risks", [])
    if not risks:
        risks = analyst_data.get("risk_matrix", [])
        
    if not risks:
        ws6.cell(row=2, column=1, value="No structured risk findings recorded")
    for r, risk in enumerate(risks, 2):
        name = risk.get("risk", risk.get("description", f"Risk {r-1}"))
        cat = str(risk.get("category") or "General")
        sev = risk.get("severity", "Not rated")
        if isinstance(sev, (int, float)) and not isinstance(sev, bool):
            sev = _severity_label(sev)
        mit = risk.get("mitigation") or ""
        if isinstance(mit, list):
            mit = ", ".join(mit)
            
        ws6.cell(row=r, column=1, value=name[:100])
        ws6.cell(row=r, column=2, value=cat.title())
        cell_sev = ws6.cell(row=r, column=3, value=sev.title() if isinstance(sev, str) else str(sev))
        ws6.cell(row=r, column=4, value=mit[:200])

        # Conditional formatting colors for severity
        if isinstance(sev, str):
            sev_low = sev.lower()
            if "critical" in sev_low or "high" in sev_low:
                cell_sev.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                cell_sev.font = Font(color="9C0006")
            elif "medium" in sev_low:
                cell_sev.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
                cell_sev.font = Font(color="9C6500")
            elif "low" in sev_low:
                cell_sev.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                cell_sev.font = Font(color="006100")

    # ─── Sheet 7: Agent Analysis ───
    ws7 = wb.create_sheet("Agent Analysis Detail")
    ws7.column_dimensions["A"].width = 25
    ws7.column_dimensions["B"].width = 15
    ws7.column_dimensions["C"].width = 15
    ws7.column_dimensions["D"].width = 80

    headers7 = ["Agent", "Confidence", "Time (ms)", "Reasoning"]
    for col, h in enumerate(headers7, 1):
        ws7.cell(row=1, column=col, value=h)
    style_header(ws7, 1, 4)

    for r, result in enumerate(agent_results, 2):
        ws7.cell(row=r, column=1, value=result.get("agent_type", "unknown").replace("_", " ").title())
        ws7.cell(row=r, column=2, value=_confidence_label(result))
        ws7.cell(row=r, column=3, value=result.get("execution_time_ms", 0))
        ws7.cell(row=r, column=4, value=result.get("reasoning", "")[:500])

    # ─── Sheet 8: Sources & References ───
    ws8 = wb.create_sheet("Sources & References")
    ws8.column_dimensions["A"].width = 14
    ws8.column_dimensions["B"].width = 16
    ws8.column_dimensions["C"].width = 18
    ws8.column_dimensions["D"].width = 20
    ws8.column_dimensions["E"].width = 36
    ws8.column_dimensions["F"].width = 70

    headers8 = ["Source ID", "Period", "Filing date", "Source type", "Title / Details", "Source URL"]
    for col, h in enumerate(headers8, 1):
        ws8.cell(row=1, column=col, value=h)
    style_header(ws8, 1, 6)

    row = 2
    brief = analyst_data.get("_evidence_brief", {})
    for source in brief.get("sources", []):
        ws8.cell(row=row, column=1, value=source.get("id", ""))
        ws8.cell(row=row, column=2, value=source.get("period", "Not recorded"))
        ws8.cell(row=row, column=3, value=source.get("filed", "Not recorded"))
        ws8.cell(row=row, column=4, value=source.get("form", "SEC filing"))
        ws8.cell(row=row, column=5, value=source.get("title") or source.get("name") or "Recorded API source")
        url_cell = ws8.cell(row=row, column=6, value=source.get("url", ""))
        if isinstance(url_cell.value, str) and url_cell.value.startswith("https://"):
            url_cell.hyperlink = url_cell.value
            url_cell.style = "Hyperlink"
        row += 1

    if provenance_records:
        for rec in provenance_records:
            ws8.cell(row=row, column=4, value="Tool Execution")
            ws8.cell(row=row, column=3, value=rec.get("timestamp", "").split("T")[0])
            ws8.cell(row=row, column=5, value=f"{rec.get('agent_name', 'System')}: {rec.get('tool_name', '')}")
            row += 1
            
    # Check for Deal/Analyst citations
    if analyst_data.get("_rag_context"):
        ws8.cell(row=row, column=4, value="Knowledge Base (RAG)")
        ws8.cell(row=row, column=5, value=f"System: {analyst_data['_rag_context'].get('chunks_used', 0)} supplemental chunks")
        row += 1

    if row == 2:
        ws8.cell(row=row, column=5, value="No source references or provenance records captured")

    ws9 = wb.create_sheet("Evidence Brief")
    ws9.column_dimensions["A"].width = 24
    ws9.column_dimensions["B"].width = 100
    ws9.append(["Section", "Recorded information"])
    style_header(ws9, 1, 2)
    ws9.append(["Recommendation", deal.get("final_recommendation") or "Not recorded"])
    ws9.append(["Score", deal.get("final_score") if deal.get("final_score") is not None else "Not recorded"])
    ws9.append(["Successful analyses", brief.get("successful_analysis_count", 0)])
    ws9.append(["Agent outputs with citations", brief.get("agents_with_citations", 0)])
    for item in brief.get("findings", []):
        ws9.append([f"Finding - {item.get('agent', 'unknown')}", item.get("text", "")])
    for item in brief.get("unknowns", []):
        ws9.append([f"Open gap - {item.get('agent', 'unknown')}", item.get("text", "")])
    ws9.append(["Interpretation", brief.get("notice", "No evidence brief was recorded.")])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ───────────────────────────────────────────────
#  3. PDF Report (Full Narrative)
# ───────────────────────────────────────────────

def generate_pdf(
    deal: Dict,
    analyst_data: Dict,
    agent_results: List[Dict],
    provenance_records: Optional[List[Dict]] = None,
    deal_stage: str = "deep_dive",
) -> bytes:
    """
    Generate High-Fidelity PDF report using ReportLab with:
    - Custom Branding & Themes
    - Dynamic Headers/Footers
    - Embedded Infographics (Football Field, Waterfall, etc.)
    - Agent-by-agent findings
    """
    buf = io.BytesIO()
    
    # Load Branding
    tenant_id = deal.get("tenant_id") or deal.get("branding_id", "default")
    brand = load_custom_branding(tenant_id)
    
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=72,
        leftMargin=72,
        topMargin=90,  # Room for header
        bottomMargin=72, # Room for footer
    )

    styles = getSampleStyleSheet()
    
    # Custom Styles
    styles.add(ParagraphStyle(
        name="CoverTitle",
        parent=styles["Heading1"],
        fontSize=32,
        textColor=colors.HexColor(brand.primary_color),
        alignment=1,
        spaceAfter=30,
    ))
    styles.add(ParagraphStyle(
        name="CoverSubtitle",
        parent=styles["Heading2"],
        fontSize=18,
        textColor=colors.HexColor("#505050"),
        alignment=1,
        spaceAfter=15,
    ))
    styles.add(ParagraphStyle(
        name="SectionTitle",
        parent=styles["Heading1"],
        fontSize=20,
        textColor=colors.HexColor(brand.primary_color),
        spaceAfter=15,
        borderPadding=(0, 0, 5, 0),
        borderWidth=0,
        borderColor=colors.HexColor(brand.secondary_color)
    ))
    styles.add(ParagraphStyle(
        name="BodyTextCustom",
        parent=styles["Normal"],
        fontSize=10,
        fontName=brand.font_family,
        textColor=colors.HexColor("#202020"),
        spaceAfter=10,
        leading=14
    ))
    styles.add(ParagraphStyle(
        name="AgentHeader",
        parent=styles["Heading2"],
        fontSize=16,
        textColor=colors.HexColor(brand.secondary_color),
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        name="BodyTextBullet",
        parent=styles["BodyTextCustom"],
        leftIndent=20,
        bulletIndent=10,
        spaceAfter=5,
    ))

    # Apply Branding
    styles = apply_branding_to_reportlab(styles, brand)

    Story = []

    # --- COVER PAGE ---
    Story.append(Spacer(1, 200))
    Story.append(Paragraph(_clean_text(deal.get("name", "Deal Analysis Report")), styles["CoverTitle"]))
    Story.append(Paragraph("Strategic Investment Analysis", styles["CoverSubtitle"]))
    Story.append(Spacer(1, 40))
    
    target = _clean_text(deal.get("target_company", "Target Company"))
    Story.append(Paragraph(f"Target: {target}", styles["CoverSubtitle"]))
    Story.append(Spacer(1, 60))
    
    date_str = datetime.now().strftime("%B %d, %Y")
    Story.append(Paragraph(f"Prepared by DealForge AI | {date_str}", styles["BodyTextCustom"]))
    Story.append(Paragraph(brand.disclaimer_text, styles["BodyTextCustom"]))
    Story.append(PageBreak())

    # --- EXECUTIVE SUMMARY ---
    Story.append(Paragraph("Executive Summary", styles["SectionTitle"]))
    
    score = deal.get("final_score")
    score_text = f"{round(score * 100)}%" if (score is not None and score <= 1) else (f"{score}" if score is not None else "Pending")

    Story.append(Paragraph(f"<b>Target Company:</b> {target}", styles["BodyTextCustom"]))
    Story.append(Paragraph(f"<b>Deal Score:</b> {score_text}", styles["BodyTextCustom"]))
    Story.append(Spacer(1, 15))

    exec_sum = analyst_data.get("executive_summary", {})
    if exec_sum:
        for phase in ["situation", "complication", "question", "answer"]:
            label = phase.upper() if phase != "answer" else "AI SYNTHESIS (NOT A RECORDED DECISION)"
            content = exec_sum.get(phase, "")
            if content:
                Story.append(Paragraph(f"<b>{label}:</b>", styles["BodyTextCustom"]))
                Story.append(Paragraph(_clean_text(content), styles["BodyTextCustom"]))
                Story.append(Spacer(1, 5))

    evidence_brief = analyst_data.get("_evidence_brief", {})
    if evidence_brief:
        from xml.sax.saxutils import escape

        Story.append(Paragraph("Evidence Brief (recorded outputs)", styles["AgentHeader"]))
        Story.append(Paragraph(
            f"Analyses: {evidence_brief.get('successful_analysis_count', 0)} successful; "
            f"{evidence_brief.get('agents_with_citations', 0)} agent outputs included citations.",
            styles["BodyTextCustom"],
        ))
        for finding in evidence_brief.get("findings", [])[:6]:
            Story.append(Paragraph(
                f"&#8226; <b>{escape(str(finding.get('agent', 'Agent')))}:</b> {escape(str(finding.get('text', '')))}",
                styles["BodyTextBullet"],
            ))
        for gap in evidence_brief.get("unknowns", [])[:4]:
            Story.append(Paragraph(
                f"Open data gap ({escape(str(gap.get('agent', 'Agent')))}): {escape(str(gap.get('text', '')))}",
                styles["BodyTextBullet"],
            ))
        Story.append(Paragraph(escape(str(evidence_brief.get("notice", ""))), styles["BodyTextCustom"]))

        points = _prioritize_financial_points(_curated_financial_points(analyst_data))
        if points:
            Story.append(Spacer(1, 10))
            Story.append(Paragraph("Financial Metrics & Evidence", styles["AgentHeader"]))
            cell_style = ParagraphStyle("EvidenceTableCell", parent=styles["BodyTextCustom"], fontSize=7.5, leading=9)
            header_style = ParagraphStyle("EvidenceTableHeader", parent=cell_style, fontName="Helvetica-Bold")
            rows = [[
                Paragraph("Metric", header_style), Paragraph("Value", header_style),
                Paragraph("Period", header_style), Paragraph("Evidence basis", header_style),
                Paragraph("Source", header_style),
            ]]
            for point in points[:24]:
                metric = str(point.get("metric", "Metric")).replace("_", " ").title()
                value = _report_percent(point.get("value")) if str(point.get("metric", "")).endswith("_percent") else _report_currency(point.get("value"))
                rows.append([
                    Paragraph(escape(metric), cell_style),
                    Paragraph(escape(value), cell_style),
                    Paragraph(escape(str(point.get("period") or "Not established")), cell_style),
                    Paragraph(escape(str(point.get("basis") or "Not recorded").replace("_", " ").title()), cell_style),
                    Paragraph(escape(str(point.get("source_id") or "No citation")), cell_style),
                ])
            evidence_table = Table(rows, colWidths=[105, 66, 64, 135, 58], repeatRows=1, hAlign="LEFT")
            evidence_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(brand.primary_color)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B7C3D0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            Story.append(evidence_table)
            Story.append(Paragraph(
                "Values without a source ID are unverified agent output; derived figures must be checked against the linked filing.",
                styles["BodyTextCustom"],
            ))
            source_rows = evidence_brief.get("sources", [])
            if source_rows:
                Story.append(Paragraph("Source References", styles["AgentHeader"]))
                for source in source_rows[:20]:
                    url = str(source.get("url") or "")
                    source_id = escape(str(source.get("id") or "Source"))
                    title = escape(str(source.get("title") or "SEC filing"))
                    if url.startswith("https://"):
                        safe_url = escape(url, {'"': "&quot;"})
                        line = f'<link href="{safe_url}" color="blue">[{source_id}] {title}</link>'
                    else:
                        line = f"[{source_id}] {title}"
                    Story.append(Paragraph(line, styles["BodyTextCustom"]))
    
    # --- SCORE WATERFALL ---
    scoring_data = deal.get("scoring_output", {})
    if scoring_data and scoring_data.get("components"):
        Story.append(Spacer(1, 10))
        Story.append(Paragraph("Deal Score Components (Score Waterfall)", styles["AgentHeader"]))
        try:
            from app.core.reports.infographic_engine import InfographicEngine
            comp_list = scoring_data.get("components", [])
            labels_sw = [c.get("name") for c in comp_list]
            values_sw = [c.get("score") for c in comp_list]
            
            # Add final score as the last bar
            total_score = scoring_data.get("total_score", sum(values_sw))
            labels_sw.append("Final Score")
            values_sw.append(total_score)
            
            chart_bytes = InfographicEngine.revenue_waterfall(
                labels=labels_sw,
                values=values_sw,
                title="Deal Score Build-up"
            )
            img = Image(io.BytesIO(chart_bytes), width=450, height=220)
            Story.append(img)
            Story.append(Spacer(1, 10))
        except Exception as e:
            logger.error("pdf_score_waterfall_error", error=str(e))
    
    # --- FINANCIAL INFOGRAPHIC ---
    fact_base = deal.get("fact_base", {})
    metrics = fact_base.get("metrics", {})
    if metrics and metrics.get("historical_revenue"):
        Story.append(Spacer(1, 10))
        Story.append(Paragraph("Financial Trajectory", styles["AgentHeader"]))
        try:
            # Prepare data for Revenue Waterfall or Trend
            rev_data = metrics.get("historical_revenue", [])
            if len(rev_data) >= 2:
                labels = [r.get("year", f"Y{i}") for i, r in enumerate(rev_data)]
                values = [r.get("amount", 0) for r in rev_data]
                
                chart_bytes = InfographicEngine.revenue_waterfall(
                    labels=labels,
                    values=values,
                    title=f"{target} Revenue Trend"
                )
                img = Image(io.BytesIO(chart_bytes), width=450, height=250)
                Story.append(img)
                Story.append(Spacer(1, 10))
        except Exception as e:
            logger.error("pdf_revenue_chart_error", error=str(e))

    # --- VALUATION SUMMARY (Football Field) ---
    valuations = deal.get("valuation_output", {}).get("valuations", [])
    if not valuations and deal.get("financial_output"):
        # Try to extract from agent output if not in top level
        valuations = deal.get("financial_output", {}).get("valuations", [])

    if valuations:
        Story.append(Paragraph("Valuation Summary (Football Field)", styles["AgentHeader"]))
        try:
            # valuations format: list of {method, low, high, mid}
            # If it's a dict of methods, convert to list
            if isinstance(valuations, dict):
                val_list = []
                for k, v in valuations.items():
                    if isinstance(v, dict) and "low" in v:
                        val_list.append({"method": k, **v})
                valuations = val_list

            if valuations:
                chart_bytes = InfographicEngine.football_field_chart(
                    valuations=valuations,
                    title="Valuation Range Analysis",
                    current_price=deal.get("current_price")
                )
                img = Image(io.BytesIO(chart_bytes), width=450, height=250)
                Story.append(img)
                Story.append(Spacer(1, 10))
        except Exception as e:
            logger.error("pdf_valuation_chart_error", error=str(e))

    Story.append(PageBreak())

    # --- AGENT FINDINGS ---
    for result in agent_results:
        agent_type = result.get("agent_type", "Agent")
        label = agent_type.replace("_", " ").title()
        reasoning = _clean_text(result.get("summary") or result.get("reasoning", "No analysis available."))[:1200]

        Story.append(Paragraph(label, styles["AgentHeader"]))

        # Format reasoning into paragraphs
        paragraphs = [p for p in reasoning.split("\n") if p.strip()]
        for p in paragraphs:
            clean_p = p.replace("<", "&lt;").replace(">", "&gt;")
            if clean_p.startswith("#"):
                Story.append(Paragraph("<b>" + clean_p.lstrip("#").strip() + "</b>", styles["BodyTextCustom"]))
            elif clean_p.startswith("-") or clean_p.startswith("*"):
                Story.append(Paragraph("• " + clean_p[1:].strip(), styles["BodyTextBullet"]))
            else:
                Story.append(Paragraph(clean_p, styles["BodyTextCustom"]))

        Story.append(Spacer(1, 15))

    # --- PROVENANCE & FOOTNOTES ---
    warnings = deal.get("consistency_warnings", [])
    if provenance_records or warnings:
        Story.append(PageBreak())
        for w in warnings:
            sev_color = "#C00000" if w.get("severity") == "material" else "#E37222"
            Story.append(
                Paragraph(
                    f"<font color='{sev_color}'><b>[{w.get('severity', 'warning').upper()}]</b></font> {w.get('message', '')} "
                    f"(Conflict: {w.get('field', 'General')} between {', '.join(w.get('agents_involved', []))})",
                    styles["BodyTextCustom"],
                )
            )
        Story.append(Spacer(1, 20))

    # Provenance Footnotes
    if provenance_records:
        Story.append(Paragraph("Provenance & Audit Trail", styles["SectionTitle"]))
        for rec in provenance_records:
            agent = rec.get("agent_name", "System")
            tool = rec.get("tool_name", "UnknownTool")
            ts = rec.get("timestamp", "").split("T")[0]
            Story.append(
                Paragraph(
                    f"• <b>{agent}</b> used <i>{tool}</i> on {ts}",
                    styles["BodyTextCustom"],
                )
            )

    doc.build(Story)

    buf.seek(0)
    return buf.read()


# ───────────────────────────────────────────────
#  4. HTML Dashboard (Interactive)
# ───────────────────────────────────────────────


def generate_html(deal: Dict, analyst_data: Dict, agent_results: List[Dict]) -> bytes:
    """
    Generate an interactive HTML dashboard with deal details and agent findings.
    """
    html = [
        "<html><head><title>DealForge Dashboard</title>",
        "<style>",
        "body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f7f6; color: #333; margin: 0; padding: 20px; }",
        ".container { max-width: 1200px; margin: auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }",
        "h1 { color: #003366; border-bottom: 2px solid #0072ce; padding-bottom: 10px; }",
        "h2 { color: #0072ce; margin-top: 30px; }",
        ".card { background: #fafafa; border: 1px solid #e0e0e0; padding: 15px; border-radius: 5px; margin-bottom: 15px; }",
        ".badge { display: inline-block; padding: 5px 10px; border-radius: 12px; background: #003366; color: #fff; font-size: 12px; font-weight: bold; }",
        "</style></head><body><div class='container'>",
    ]

    html.append(f"<h1>{deal.get('name', 'Deal Analysis Dashboard')}</h1>")
    html.append(f"<p><strong>Target:</strong> {deal.get('target_company', 'N/A')}</p>")
    html.append(f"<p><strong>Industry:</strong> {deal.get('industry', 'N/A')}</p>")

    score = deal.get("final_score")
    score_text = f"{round(score * 100)}%" if score is not None else "Pending"
    html.append(
        f"<p><strong>Score:</strong> <span class='badge'>{score_text}</span></p>"
    )

    exec_sum = analyst_data.get("executive_summary", {})
    if exec_sum:
        html.append("<h2>Executive Summary</h2>")
        html.append(
            f"<div class='card'><p><b>Situation:</b> {exec_sum.get('situation', '')}</p>"
        )
        html.append(f"<p><b>Complication:</b> {exec_sum.get('complication', '')}</p>")
        html.append(f"<p><b>Question:</b> {exec_sum.get('question', '')}</p>")
        html.append(
            f"<p><b>AI Synthesis (not a recorded decision):</b> {exec_sum.get('answer', '')}</p></div>"
        )

    html.append("<h2>Agent Findings</h2>")
    for r in agent_results:
        agent_type = r.get("agent_type", "Agent").replace("_", " ").title()
        reasoning = r.get("reasoning", "").replace("\n", "<br/>")
        conf = _confidence_label(r)
        html.append(
            f"<div class='card'><h3>{agent_type} <span class='badge'>{conf} Confidence</span></h3>"
        )
        html.append(f"<p>{reasoning}</p></div>")

    html.append("</div></body></html>")
    return "".join(html).encode("utf-8")


# ───────────────────────────────────────────────
#  5. DOCX Report (Professional Word Document)
# ───────────────────────────────────────────────


try:
    from docx import Document as DocxDocument
    from docx.shared import Inches as DocxInches, Pt as DocxPt, Cm as DocxCm, RGBColor as DocxRGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.section import WD_ORIENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    HAS_PYTHON_DOCX = True
except ImportError:
    HAS_PYTHON_DOCX = False


def generate_docx(
    deal: Dict,
    analyst_data: Dict,
    agent_results: List[Dict],
    provenance_records: Optional[List[Dict]] = None,
    deal_stage: str = "deep_dive",
) -> bytes:
    """
    Generate a professional McKinsey-style Word document with:
    - Cover page with title, target company, date, CONFIDENTIAL watermark
    - Table of Contents placeholder
    - Executive Summary (SCQ framework)
    - Key Financial Metrics
    - Per-agent findings sections
    - Risk Matrix table
    - References/Provenance section
    - Professional formatting: Calibri font, Heading 1/2/3 hierarchy
    """
    if not HAS_PYTHON_DOCX:
        raise ImportError(
            "python-docx is required for DOCX generation. "
            "Install it with: pip install python-docx"
        )

    doc = DocxDocument()

    blueprint = analyst_data.get("_report_blueprint", {})
    document_qa = analyst_data.get("_document_qa", {})
    section = doc.sections[0]
    if blueprint.get("page_orientation") == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width
    section.top_margin = DocxInches(0.7)
    section.bottom_margin = DocxInches(0.65)
    section.left_margin = DocxInches(0.8)
    section.right_margin = DocxInches(0.8)

    # ─── Global font defaults ───
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Calibri"
    font.size = DocxPt(11)
    font.color.rgb = DocxRGBColor(0x20, 0x20, 0x20)
    font_size = {"compact": 10, "standard": 11, "detailed": 11}.get(blueprint.get("density"), 11)
    font.size = DocxPt(font_size)
    style.paragraph_format.space_after = DocxPt(5 if font_size == 10 else 7)
    style.paragraph_format.line_spacing = 1.08

    for level in (1, 2, 3):
        heading_style = doc.styles[f"Heading {level}"]
        heading_style.font.name = "Calibri"
        heading_style.font.color.rgb = DocxRGBColor(0x00, 0x33, 0x66)
        if level == 1:
            heading_style.font.size = DocxPt(24)
        elif level == 2:
            heading_style.font.size = DocxPt(18)
        else:
            heading_style.font.size = DocxPt(14)

    # ─── Helper: set cell shading ───
    def _set_cell_shading(cell, color_hex: str):
        """Apply background shading to a table cell."""
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), color_hex)
        shading.set(qn("w:val"), "clear")
        cell._tc.get_or_add_tcPr().append(shading)

    # ─── Helper: add styled table ───
    def _style_header_row(table):
        """Apply dark navy header styling to the first row of a table."""
        for cell in table.rows[0].cells:
            _set_cell_shading(cell, "003366")
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in paragraph.runs:
                    run.font.color.rgb = DocxRGBColor(0xFF, 0xFF, 0xFF)
                    run.font.bold = True
                    run.font.size = DocxPt(11)
                    run.font.name = "Calibri"

    # ═══════════════════════════════════════════
    #  COVER PAGE
    # ═══════════════════════════════════════════

    # CONFIDENTIAL watermark (light gray, centered)
    p_conf = doc.add_paragraph()
    p_conf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_conf = p_conf.add_run("CONFIDENTIAL")
    run_conf.font.size = DocxPt(36)
    run_conf.font.color.rgb = DocxRGBColor(0xC0, 0xC0, 0xC0)
    run_conf.font.bold = True
    run_conf.font.name = "Calibri"

    # Spacer
    for _ in range(4):
        doc.add_paragraph()

    # Title
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_title = p_title.add_run(
        _clean_text(deal.get("target_company") or deal.get("name") or "Deal Analysis Report")
    )
    run_title.font.size = DocxPt(32)
    run_title.font.bold = True
    run_title.font.color.rgb = DocxRGBColor(0x00, 0x33, 0x66)
    run_title.font.name = "Calibri"

    # Subtitle
    p_sub = doc.add_paragraph()
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_sub = p_sub.add_run("Deal Analysis & Diligence Brief")
    run_sub.font.size = DocxPt(18)
    run_sub.font.color.rgb = DocxRGBColor(0x50, 0x50, 0x50)
    run_sub.font.name = "Calibri"

    # Target company
    target = _clean_text(deal.get("target_company", "Target Company"))
    p_target = doc.add_paragraph()
    p_target.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_target = p_target.add_run(f"Target: {target}")
    run_target.font.size = DocxPt(16)
    run_target.font.color.rgb = DocxRGBColor(0x50, 0x50, 0x50)
    run_target.font.name = "Calibri"

    doc.add_paragraph()  # spacer

    # Date and attribution
    date_str = datetime.now().strftime("%B %d, %Y")
    p_date = doc.add_paragraph()
    p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_date = p_date.add_run(f"Prepared by DealForge AI | {date_str}")
    run_date.font.size = DocxPt(12)
    run_date.font.color.rgb = DocxRGBColor(0x80, 0x80, 0x80)
    run_date.font.name = "Calibri"

    # Page break after cover
    doc.add_page_break()

    # ═══════════════════════════════════════════
    #  TABLE OF CONTENTS
    # ═══════════════════════════════════════════

    doc.add_heading("Table of Contents", level=1)
    evidence_brief = analyst_data.get("_evidence_brief", {})
    risk_matrix = next((
        (item.get("data") or {}).get("risks", []) for item in agent_results
        if item.get("agent_type") == "risk_assessor"
        and isinstance(item.get("data"), dict)
        and isinstance((item.get("data") or {}).get("risks"), list)
        and (item.get("data") or {}).get("risks")
    ), [])
    valuations = (deal.get("valuation_output") or {}).get("valuations", []) or (deal.get("financial_output") or {}).get("valuations", [])
    historical_revenue = ((deal.get("fact_base") or {}).get("metrics", {}) or {}).get("historical_revenue")
    has_visuals = bool(valuations or historical_revenue)
    toc_sections = ["Executive Summary"]
    if evidence_brief.get("data_points"):
        toc_sections.append("Financial Metrics and Source Status")
    if agent_results:
        toc_sections.append("Agent Findings")
    if risk_matrix:
        toc_sections.append("Risk Assessment")
    if has_visuals:
        toc_sections.append("Visual Analysis")
    toc_sections.append("References and Provenance")
    for section in toc_sections:
        entry = doc.add_paragraph(style="List Bullet")
        entry.paragraph_format.space_after = DocxPt(2)
        entry.add_run(section).font.size = DocxPt(10)

    doc.add_page_break()

    # ═══════════════════════════════════════════
    #  EXECUTIVE SUMMARY (SCQ Framework)
    # ═══════════════════════════════════════════

    if blueprint or document_qa:
        doc.add_heading("Evidence Coverage & Review Status", level=1)
        brief = analyst_data.get("_evidence_brief", {})
        review_status = "Human review required" if document_qa.get("status") == "review_required" or brief.get("unknowns") else "Ready for human review"
        doc.add_paragraph(f"Status: {review_status}")
        doc.add_paragraph(
            f"Recorded successful analyses: {brief.get('successful_analysis_count', 0)}; "
            f"source records: {len(brief.get('sources', []))}; "
            f"curated financial data points: {len(brief.get('data_points', []))}."
        )
        if document_qa.get("warnings"):
            for warning in document_qa["warnings"]:
                doc.add_paragraph(_clean_text(warning), style="List Bullet")
        conflicts = analyst_data.get("_evidence_brief", {}).get("metric_conflicts", [])
        if conflicts:
            doc.add_heading("Metric reconciliation required", level=2)
            for conflict in conflicts:
                reports = "; ".join(
                    f"{item['agent']}: {item['value']}" for item in conflict.get("reports", [])
                )
                doc.add_paragraph(
                    f"{conflict.get('metric', 'Metric')}: {reports}. Reconcile units, period, and source before relying on either value.",
                    style="List Bullet",
                )
        doc.add_paragraph("Model-generated analysis is not independent verification. Confirm material claims against primary records before relying on them.")
        doc.add_page_break()

    doc.add_heading("1. Executive Summary", level=1)

    score = deal.get("final_score")
    score_text = f"{round(score * 100)}%" if score is not None else "Pending"
    industry = _clean_text(deal.get("industry") or "Not recorded").replace("_", " ").title()

    # Deal overview table
    overview_table = doc.add_table(rows=4, cols=2, style="Table Grid")
    overview_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    overview_data = [
        ("Target Company", target),
        ("Industry", industry),
        ("Deal Score", score_text),
        ("Date", date_str),
    ]
    for i, (label, value) in enumerate(overview_data):
        overview_table.rows[i].cells[0].text = label
        overview_table.rows[i].cells[1].text = _clean_text(str(value))
        for paragraph in overview_table.rows[i].cells[0].paragraphs:
            for run in paragraph.runs:
                run.font.bold = True
                run.font.name = "Calibri"
                run.font.size = DocxPt(11)
        for paragraph in overview_table.rows[i].cells[1].paragraphs:
            for run in paragraph.runs:
                run.font.name = "Calibri"
                run.font.size = DocxPt(11)

    doc.add_paragraph()  # spacer

    chat_context = analyst_data.get("_chat_context", {})
    if chat_context.get("mandate") or chat_context.get("chat_summary"):
        doc.add_heading("Mandate & Chat Context", level=2)
        if chat_context.get("mandate"):
            doc.add_paragraph("Latest user request (conversation record):", style="Heading 3")
            doc.add_paragraph(_clean_text(chat_context["mandate"]))
        if chat_context.get("chat_summary"):
            doc.add_paragraph("DealForge chat synthesis (not independently verified):", style="Heading 3")
            doc.add_paragraph(_clean_text(chat_context["chat_summary"]))
        doc.add_paragraph(
            "Chat text is included for scope and continuity only. It is not a primary source; "
            "verify every factual claim against the cited records below."
        )

    # SCQ Framework
    exec_sum = analyst_data.get("executive_summary", {})
    if exec_sum:
        scq_sections = [
            ("Situation", exec_sum.get("situation", "Not established in the saved analysis.")),
            ("Complication", exec_sum.get("complication", "No complication recorded in the saved analysis.")),
            ("Question", exec_sum.get("question", "Confirm the decision question with the deal team.")),
            ("AI Synthesis (not a recorded decision)", exec_sum.get(
                "answer", deal.get("final_recommendation") or "No recommendation recorded; human review required."
            )),
        ]
        for heading_text, body_text in scq_sections:
            doc.add_heading(heading_text, level=2)
            doc.add_paragraph(_clean_text(str(body_text)))
    else:
        # Fallback narrative
        rec = deal.get("final_recommendation")
        if not deal.get("final_recommendation"):
            rec = "No recommendation recorded; human review required."
        doc.add_heading("Recommendation", level=2)
        doc.add_paragraph(_clean_text(str(rec)))

    takeaways = analyst_data.get("key_takeaways", [])
    if takeaways:
        doc.add_heading("Decision-Relevant Takeaways", level=2)
        for takeaway in takeaways[:4]:
            if isinstance(takeaway, dict):
                title = _clean_text(str(takeaway.get("title") or "Evidence-backed takeaway"))
                description = _clean_text(str(takeaway.get("description") or ""))
                doc.add_paragraph(f"{title}: {description}", style="List Bullet")

    if evidence_brief:
        doc.add_heading("Evidence Brief (recorded outputs)", level=2)
        doc.add_paragraph(
            f"{evidence_brief.get('successful_analysis_count', 0)} successful analyses; "
            f"{evidence_brief.get('agents_with_citations', 0)} agent outputs included citations."
        )
        for finding in evidence_brief.get("findings", [])[:6]:
            doc.add_paragraph(
                f"{finding.get('text', '')} [Source agent: {finding.get('agent', 'unknown')}]",
                style="List Bullet",
            )
        if evidence_brief.get("unknowns"):
            doc.add_paragraph("Open data gaps", style="Heading 3")
            for gap in evidence_brief["unknowns"][:4]:
                doc.add_paragraph(
                    f"{gap.get('text', '')} [Source agent: {gap.get('agent', 'unknown')}]",
                    style="List Bullet",
                )
        doc.add_paragraph(evidence_brief.get("notice", ""))

    # Score Waterfall in Word
    scoring_data = deal.get("scoring_output", {})
    if scoring_data and scoring_data.get("components"):
        try:
            from app.core.reports.infographic_engine import InfographicEngine
            doc.add_heading("Deal Score Composition", level=2)
            comp_list = scoring_data.get("components", [])
            labels_sw = [c.get("name") for c in comp_list]
            values_sw = [c.get("score") for c in comp_list]
            
            labels_sw.append("Final Score")
            values_sw.append(scoring_data.get("total_score", sum(values_sw)))
            
            chart_bytes = InfographicEngine.revenue_waterfall(
                labels=labels_sw,
                values=values_sw,
                title="Deal Score Composition"
            )
            from io import BytesIO
            img_stream = BytesIO(chart_bytes)
            doc.add_picture(img_stream, width=DocxInches(5.5))
            doc.add_paragraph("Visual breakdown of factors contributing to the final deal score.")
        except Exception as e:
            logger.error("docx_score_waterfall_error", error=str(e))

    # ═══════════════════════════════════════════
    #  KEY FINANCIAL METRICS
    # ═══════════════════════════════════════════

    fin_synth = analyst_data.get("financial_synthesis", {})
    narrative = fin_synth.get("narrative", "") if fin_synth else ""

    if evidence_brief.get("data_points"):
        doc.add_page_break()
        doc.add_heading("2. Financial Metrics and Source Status", level=1)
        metrics_table = doc.add_table(rows=1, cols=5, style="Table Grid")
        metrics_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for cell, label in zip(metrics_table.rows[0].cells, ("Metric", "Value", "Period", "Evidence basis", "Source")):
            cell.text = label
        _style_header_row(metrics_table)
        for point in _prioritize_financial_points(evidence_brief["data_points"])[:30]:
            metric_name = str(point.get("metric", "Unknown"))
            metric_value = point.get("value")
            formatted_value = _report_percent(metric_value) if metric_name.endswith("_percent") else _report_currency(metric_value)
            values = (
                metric_name.replace("_", " ").replace("yoy", "YoY").title(),
                formatted_value,
                str(point.get("period") or "Not established"),
                str(point.get("basis") or "Not recorded").replace("_", " ").title(),
                str(point.get("source_id") or "No citation recorded"),
            )
            for cell, value in zip(metrics_table.add_row().cells, values):
                cell.text = value
        doc.add_paragraph("Values without a linked source are unverified agent output, not established reported facts.")

    if narrative and evidence_brief.get("data_points"):
        doc.add_heading("Recorded Financial Synthesis (verify against sources)", level=2)
        doc.add_paragraph(_clean_text(str(narrative)))

    # ═══════════════════════════════════════════
    #  AGENT FINDINGS
    # ═══════════════════════════════════════════

    if agent_results:
        doc.add_page_break()
        doc.add_heading("3. Agent Findings", level=1)

    for idx, result in enumerate(agent_results, 1):
        agent_type = result.get("agent_type", "Agent")
        label = agent_type.replace("_", " ").title()
        confidence_pct = _confidence_label(result)
        provider = result.get("provider", "unknown")
        reasoning = _clean_text(result.get("summary") or result.get("reasoning", "No analysis data available."))[:1200]

        doc.add_heading(
            f"3.{idx}  {label}", level=2
        )

        # Confidence & provider metadata
        p_meta = doc.add_paragraph()
        run_meta = p_meta.add_run(
            f"Confidence: {confidence_pct}  |  Provider: {provider}"
        )
        run_meta.font.size = DocxPt(10)
        run_meta.font.italic = True
        run_meta.font.color.rgb = DocxRGBColor(0x60, 0x60, 0x60)
        run_meta.font.name = "Calibri"

        # Reasoning body — split into paragraphs
        paragraphs = [p for p in reasoning.split("\n") if p.strip()]
        for para_text in paragraphs:
            clean_para = para_text.strip()
            if clean_para.startswith("#"):
                # Sub-heading within agent output
                doc.add_heading(
                    clean_para.lstrip("#").strip(), level=3
                )
            else:
                doc.add_paragraph(clean_para)

    # ═══════════════════════════════════════════
    #  RISK MATRIX
    # ═══════════════════════════════════════════

    if risk_matrix:
        doc.add_page_break()
        doc.add_heading("4. Risk Assessment", level=1)
        # Create table with Evidence column
        risk_table = doc.add_table(rows=1, cols=4, style="Table Grid")
        risk_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        hdr_cells = risk_table.rows[0].cells
        hdr_cells[0].text = "Risk"
        hdr_cells[1].text = "Severity"
        hdr_cells[2].text = "Mitigation"
        hdr_cells[3].text = "Evidence"
        _style_header_row(risk_table)

        for rm in risk_matrix:
            row_cells = risk_table.add_row().cells
            row_cells[0].text = _clean_text(str(rm.get("risk", rm.get("title", "N/A"))))
            row_cells[1].text = _clean_text(str(rm.get("severity", "N/A")))
            row_cells[2].text = _clean_text(str(rm.get("mitigation", "N/A")))
            row_cells[3].text = _clean_text(str(rm.get("evidence", "Diligence pending.")))
            
            for cell in row_cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.name = "Calibri"
                        run.font.size = DocxPt(10)

    # ═══════════════════════════════════════════
    #  INFOGRAPHIC APPENDICES
    # ═══════════════════════════════════════════
    if has_visuals:
        doc.add_page_break()
        doc.add_heading("5. Visual Analysis", level=1)
        from app.core.reports.infographic_engine import InfographicEngine
    
    if valuations:
        try:
            doc.add_heading("5.1 Valuation Range Analysis (Football Field)", level=2)
            if isinstance(valuations, dict):
                valuations = [{"method": k, **v} for k, v in valuations.items() if isinstance(v, dict)]
            
            chart_bytes = InfographicEngine.football_field_chart(
                valuations=valuations,
                title="Valuation Range Analysis",
                current_price=deal.get("current_price")
            )
            from io import BytesIO
            img_stream = BytesIO(chart_bytes)
            doc.add_picture(img_stream, width=DocxInches(6))
            doc.add_paragraph("Figure 1: Comparison of multiple valuation methodologies.")
        except Exception as e:
            logger.error("docx_football_field_error", error=str(e))

    # Revenue Waterfall in Word
    fact_base = deal.get("fact_base", {})
    metrics_fb = fact_base.get("metrics", {})
    if metrics_fb.get("historical_revenue"):
        try:
            doc.add_heading("5.2 Financial Growth Trajectory", level=2)
            rev_data = metrics_fb.get("historical_revenue", [])
            labels_wf = [r.get("year", f"Y{i}") for i, r in enumerate(rev_data)]
            values_wf = [r.get("amount", 0) for i, r in enumerate(rev_data)]
            
            chart_bytes = InfographicEngine.revenue_waterfall(labels_wf, values_wf, "Revenue Trajectory")
            from io import BytesIO
            img_stream = BytesIO(chart_bytes)
            doc.add_picture(img_stream, width=DocxInches(6))
            doc.add_paragraph("Figure 2: Historical revenue bridge and growth build-up.")
        except Exception as e:
            logger.error("docx_waterfall_error", error=str(e))
    doc.add_page_break()

    # ═══════════════════════════════════════════
    #  REFERENCES / PROVENANCE
    # ═══════════════════════════════════════════

    doc.add_heading("References and Provenance", level=1)

    evidence_sources = analyst_data.get("_evidence_brief", {}).get("sources", [])
    if evidence_sources:
        doc.add_heading("Source register", level=2)
        for source in evidence_sources:
            source_id = _clean_text(str(source.get("id", "Source")))
            source_title = _clean_text(str(source.get("title") or source.get("name") or "Recorded API source"))
            period = _clean_text(str(source.get("period") or "period not recorded"))
            url = _clean_text(str(source.get("url") or "URL not recorded"))
            filed = _clean_text(str(source.get("filed") or "filing date not recorded"))
            doc.add_paragraph(f"[{source_id}] {source_title} | Period: {period} | Filed: {filed} | {url}", style="List Bullet")

    ref_counter = 0

    # Data consistency warnings
    warnings = deal.get("consistency_warnings", [])
    if warnings:
        doc.add_heading("Data Consistency Notes", level=2)
        for w in warnings:
            ref_counter += 1
            sev = _clean_text(str(w.get("severity", "warning"))).upper()
            msg = _clean_text(str(w.get("message", "")))
            field = _clean_text(str(w.get("field", "General")))
            agents_involved = w.get("agents_involved", [])
            agents_str = ", ".join(str(a) for a in agents_involved) if agents_involved else "N/A"
            doc.add_paragraph(
                f"[{ref_counter}] [{sev}] {msg} (Field: {field}, Agents: {agents_str})",
                style="List Number",
            )

    # Provenance records
    if provenance_records:
        doc.add_heading("Data Integration Provenance", level=2)
        for rec in provenance_records:
            ref_counter += 1
            agent = _clean_text(str(rec.get("agent_name", "System")))
            tool = _clean_text(str(rec.get("tool_name", "UnknownTool")))
            ts = _clean_text(str(rec.get("timestamp", "")).split("T")[0])
            doc.add_paragraph(
                f"[{ref_counter}] {agent} -- data retrieved via {tool} on {ts}",
                style="List Number",
            )

    # RAG context references
    rag_ctx = analyst_data.get("_rag_context")
    if rag_ctx:
        chunks_used = rag_ctx.get("chunks_used", 0)
        doc.add_paragraph(
            f"Supplemental knowledge-base context: {chunks_used} chunks used. This is context, not a primary-source citation.",
            style="List Bullet",
        )

    if ref_counter == 0:
        doc.add_paragraph(
            "No provenance records or references were captured for this report."
        )

    # ─── Footer note ───
    doc.add_paragraph()  # spacer
    p_footer = doc.add_paragraph()
    p_footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_footer = p_footer.add_run(
        "This report was generated by DealForge AI Multi-Agent System. "
        "All data should be independently verified before making investment decisions."
    )
    run_footer.font.size = DocxPt(9)
    run_footer.font.italic = True
    run_footer.font.color.rgb = DocxRGBColor(0x80, 0x80, 0x80)
    run_footer.font.name = "Calibri"

    # ─── Save to bytes buffer ───
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _severity_label(value: Any) -> str:
    """Agents report severity as words or numbers (1-5 or 1-10); render a label.

    The deck generator called .lower() on it, so a numeric severity crashed
    the PPTX and with it the whole legacy bundle.
    """
    if isinstance(value, bool) or value is None:
        return "Not rated"
    if isinstance(value, (int, float)):
        score = float(value) * 2 if value <= 5 else float(value)
        return "Critical" if score >= 9 else "High" if score >= 7 else "Medium" if score >= 4 else "Low"
    return str(value)


def _report_number(value: Any) -> str:
    if value is None:
        return "Not established"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.2f}" if isinstance(value, float) and not value.is_integer() else f"{value:,.0f}"
    return str(value)


def _report_percent(value: Any) -> str:
    return "Not established" if value is None else f"{value}%"


def _report_currency(value: Any) -> str:
    if value is None:
        return "Not established"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if abs(value) >= 1_000_000_000:
            return f"${value / 1_000_000_000:,.1f}bn"
        if abs(value) >= 1_000_000:
            return f"${value / 1_000_000:,.1f}m"
        return f"${value:,.0f}"
    return str(value)


def generate_structured_analysis_docx(deal: Dict, todo_list: Dict) -> bytes:
    """Render saved task results as a concise, evidence-oriented Word report."""
    if not HAS_PYTHON_DOCX:
        raise ImportError("python-docx is required for DOCX generation.")

    doc = DocxDocument()
    normal = doc.styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = DocxPt(10)
    for level in (1, 2):
        doc.styles[f"Heading {level}"].font.name = "Aptos Display"

    company = deal.get("target_company") or todo_list.get("company_name") or "Target company"
    doc.core_properties.title = f"DealForge Analysis Report - {company}"
    doc.core_properties.subject = "Evidence-backed analysis results"
    doc.add_heading(str(company), 0)
    doc.add_paragraph("Analysis Report | Evidence-backed results", style="Subtitle")
    doc.add_paragraph(f"Deal ID: {deal.get('id', '')}")
    doc.add_paragraph(f"Prepared: {datetime.now().strftime('%B %d, %Y')}")

    doc.add_heading("Analysis Status", level=1)
    status_table = doc.add_table(rows=0, cols=2)
    status_table.style = "Light Shading Accent 1"
    for label, value in (
        ("Workflow status", todo_list.get("status", "unknown")),
        ("Tasks completed", f"{sum(1 for item in todo_list.get('items', []) if item.get('status') == 'done')} / {len(todo_list.get('items', []))}"),
        ("Deal score", deal.get("final_score") if deal.get("final_score") is not None else "Not scored"),
    ):
        cells = status_table.add_row().cells
        cells[0].text = str(label)
        cells[1].text = str(value)

    doc.add_heading("Agent Findings", level=1)
    for index, item in enumerate(todo_list.get("items", []), start=1):
        agent = str(item.get("assigned_agent") or "Unassigned agent").replace("_", " ").title()
        doc.add_heading(f"{index}. {agent}", level=2)
        doc.add_paragraph(str(item.get("title") or "Analysis task"))
        doc.add_paragraph(f"Task status: {item.get('status', 'unknown')}")
        result = item.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (TypeError, ValueError):
                pass
        if isinstance(result, dict):
            historical = result.get("historical_financials")
            if isinstance(historical, list) and historical:
                doc.add_paragraph(
                    f"Source: {result.get('data_source', 'Not recorded')}. "
                    f"Confidence: {result.get('confidence_basis', 'Not calibrated')}.",
                    style="Normal",
                )
                findings = result.get("key_findings", [])
                if findings:
                    doc.add_heading("Key Findings", level=3)
                    for finding in findings:
                        if isinstance(finding, dict) and finding.get("text"):
                            doc.add_paragraph(str(finding["text"]), style="List Bullet")
                sources = result.get("sources", [])
                source_indexes = {
                    (str(source.get("period")), source.get("url")): index
                    for index, source in enumerate(sources, start=1)
                    if isinstance(source, dict) and source.get("url")
                }
                headers = ["Fiscal year", "Period end", "Revenue", "YoY growth", "Net income", "Filing"]
                table = doc.add_table(rows=1, cols=len(headers))
                table.style = "Light Shading Accent 1"
                for cell, label in zip(table.rows[0].cells, headers):
                    cell.text = label
                for period in historical:
                    cells = table.add_row().cells
                    values = (
                        period.get("fiscal_year") or period.get("period") or "Not established",
                        period.get("period_end_date") or "Not established",
                        _report_currency(period.get("revenue")),
                        _report_percent(period.get("revenue_yoy_percent")),
                        _report_currency(period.get("net_income")),
                        f"[{source_indexes.get((str(period.get('period')), period.get('source_url')), '?')}] "
                        f"{period.get('filing_form', 'Filing')} filed {period.get('filing_date', 'date not recorded')}",
                    )
                    for cell, value in zip(cells, values):
                        cell.text = value

                doc.add_heading("Latest-period metrics", level=3)
                metrics = []
                for section_name, fields in (
                    ("Cash flow", ("operating_cash_flow", "capital_expenditures", "free_cash_flow")),
                    ("Balance sheet", ("cash", "long_term_debt")),
                    ("Profitability", ("gross_profit", "operating_income", "net_income", "ebitda")),
                    ("Valuation", ("dcf_estimate", "multiple_estimate")),
                ):
                    section = result.get(section_name.lower().replace(" ", "_"), {})
                    for field in fields:
                        if field in section:
                            label = field.replace("_", " ").title()
                            period_key = {
                                "operating_cash_flow": "fiscal_year",
                                "capital_expenditures": "capital_expenditures_fiscal_year",
                                "free_cash_flow": "free_cash_flow_fiscal_year",
                                "cash": "cash_fiscal_year",
                                "long_term_debt": "debt_fiscal_year",
                            }.get(field)
                            suffix = f" ({section[period_key]})" if period_key and section.get(period_key) else ""
                            value = section[field]
                            financial_fields = {
                                "operating_cash_flow", "capital_expenditures", "free_cash_flow", "cash",
                                "long_term_debt", "gross_profit", "operating_income", "net_income",
                                "ebitda", "dcf_estimate", "multiple_estimate",
                            }
                            metrics.append((label + suffix, _report_currency(value) if field in financial_fields else _report_number(value)))
                if metrics:
                    metric_table = doc.add_table(rows=0, cols=2)
                    metric_table.style = "Light Shading Accent 1"
                    for label, value in metrics:
                        cells = metric_table.add_row().cells
                        cells[0].text, cells[1].text = label, value

                doc.add_heading("Limitations and open items", level=3)
                limitations = result.get("data_limitations", [])
                for limitation in limitations:
                    doc.add_paragraph(str(limitation), style="List Bullet")
                if sources:
                    doc.add_heading("Sources", level=3)
                    for index, source in enumerate(sources, start=1):
                        doc.add_paragraph(
                            f"[{index}] {source.get('title', 'Source')} | FY{source.get('period', 'not specified')} | "
                            f"filed {source.get('filed', 'date not recorded')} | {source.get('url', 'URL not recorded')}",
                            style="List Bullet",
                        )
            else:
                doc.add_paragraph(str(result.get("reasoning") or "Recorded result summary."))
                omit = {"calculations", "raw_data", "financial_data", "sources", "tool_results"}
                concise = [
                    (str(key).replace("_", " ").title(), value)
                    for key, value in result.items()
                    if key not in omit and not isinstance(value, (dict, list))
                ]
                for section, values in result.items():
                    if section in omit or not isinstance(values, dict):
                        continue
                    for key, value in values.items():
                        if not isinstance(value, (dict, list)):
                            concise.append((f"{section.replace('_', ' ').title()} - {key.replace('_', ' ').title()}", value))
                if concise:
                    table = doc.add_table(rows=0, cols=2)
                    table.style = "Light Shading Accent 1"
                    for label, value in concise:
                        cells = table.add_row().cells
                        cells[0].text = label
                        cells[1].text = "Not established" if value is None else _report_number(value)
        elif result is not None:
            doc.add_paragraph(str(result))
        else:
            doc.add_paragraph("No structured result was recorded for this task.")

    doc.add_heading("Interpretation Notes", level=1)
    doc.add_paragraph(
        "This report contains only values and sources recorded by the analysis. "
        "A missing or null value means it was not established; it is not zero. "
        "User-provided inputs and model-generated assessments have not been independently verified."
    )
    doc.add_paragraph(
        "Verify source documents, reporting periods, definitions, and assumptions before relying on these findings."
    )

    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


class KBReportEnricher:
    """Pull formatting standards and data from Knowledge Base."""
    
    def __init__(self, pageindex_client):
        self.kb = pageindex_client
        self.citations = []  # Accumulated references
    
    async def get_formatting_context(self, deal_name: str) -> dict:
        """Query KB for report format definitions and templates."""
        try:
            chunks = await self.kb.query(
                "investment memo format structure executive summary",
                top_k=3
            )
            for c in chunks:
                self.citations.append({
                    "source": c.metadata.get("filename", "KB Document"),
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "excerpt": c.content[:200]
                })
            return {"formatting_guidance": [c.content for c in chunks]}
        except Exception as e:
            logger.warning(f"KB formatting query failed: {e}")
            return {"formatting_guidance": []}
    
    async def get_company_context(self, company: str, industry: str) -> dict:
        """Query KB for company/industry-specific data from uploaded docs."""
        try:
            chunks = await self.kb.query(
                f"{company} {industry} financial analysis market",
                top_k=5
            )
            for c in chunks:
                self.citations.append({
                    "source": c.metadata.get("filename", "KB Document"),
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "excerpt": c.content[:200]
                })
            return {
                "kb_insights": [c.content for c in chunks],
                "kb_sources": [c.metadata for c in chunks]
            }
        except Exception as e:
            logger.warning(f"KB company query failed: {e}")
            return {"kb_insights": [], "kb_sources": []}
    
    def get_references(self) -> list:
        """Return deduplicated citation list for the references section."""
        seen = set()
        unique = []
        for c in self.citations:
            key = f"{c['source']}:p{c['page']}"
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return sorted(unique, key=lambda x: x.get('source', 'Unknown'))
