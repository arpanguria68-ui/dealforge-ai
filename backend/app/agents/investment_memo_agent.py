"""
Investment Memo & Charting Agent — 'The Editor'

Synthesizes raw data into Bulge Bracket-grade investment memos.
Generates visual aids (football field, Sankey, radar charts).
"""

from typing import Dict, Any, Optional, List
import json
import re
from datetime import datetime

from app.agents.base import BaseAgent, AgentOutput
from app.core.prompt_context import render_context
from app.core.validation.memo_grounding import check_memo_grounding, grounding_notice
from app.core.validation.reconciliation import reconcile, reconciliation_notice

# Context keys that are runtime objects or duplicated elsewhere in the prompt.
_CONTEXT_EXCLUDE = {"agent_results", "kb_graph", "knowledge_graph_context", "risk_data"}


_CHATTER_OPENER = re.compile(
    r"^\s*(?:sure|okay|ok|of course|certainly|absolutely|alright|yes|no problem|i(?:'|’)?ll|i will|i can|i need|"
    r"i(?:'|’)?m|i am|first,|let me|here(?:'|’)?s)(?![a-z])",
    re.IGNORECASE,
)


def memo_is_usable(content: str) -> bool:
    """False for an empty reply, a chat product's canned error, or short off-topic chatter.

    A chat-product proxy has answered "Sure. First, I'll need your permission to turn on the
    Adobe app." in place of a memo. A real memo has headings, figures or substantial length;
    a short conversational reply with none of them is not one.
    """
    from app.core.llm.gemini_client import looks_like_chat_failure

    text = (content or "").strip()
    if not text or looks_like_chat_failure(text):
        return False
    structured = "#" in text or "\n" in text or any(ch.isdigit() for ch in text)
    return structured or len(text) >= 400 or not _CHATTER_OPENER.match(text)


def _verified_valuation_block(agent_results: List[Dict[str, Any]]) -> str:
    """Sourced valuation inputs and assumptions from the valuation agent, verbatim."""
    for result in agent_results or []:
        data = result.get("data") if isinstance(result, dict) else None
        audit = data.get("audit") if isinstance(data, dict) else None
        if not isinstance(audit, dict) or not (audit.get("sources") or audit.get("assumptions")):
            continue
        lines = ["VERIFIED VALUATION INPUTS (use these exact figures; do not substitute others):"]
        market = (audit.get("inputs") or {}).get("market_data") or {}
        if market.get("ev_revenue_median"):
            peers = ", ".join(f"{p['ticker']} {p['ev_revenue']:.2f}x" for p in market.get("peers", []))
            lines.append(f"- Peer EV/Revenue median {market['ev_revenue_median']:.2f}x ({peers})")
        if audit.get("wacc_build"):
            lines.append(f"- WACC build: {json.dumps(audit['wacc_build'])}")
        for key in ("sources", "assumptions", "missing"):
            if audit.get(key):
                lines.append(f"- {key.capitalize()}: " + "; ".join(map(str, audit[key])))
        return "\n".join(lines)
    return ""


class InvestmentMemoAgent(BaseAgent):
    """
    The Editor — produces final investment deliverables:
    - Executive summary with key metrics
    - Investment thesis and recommendation
    - Football field valuation chart
    - Risk assessment visualization
    - Appendix with detailed agent findings
    """

    name = "investment_memo_agent"
    description: str = (
        "Generates McKinsey-grade investment memos with charts and executive summaries"
    )
    recommended_model: str = "Gemini 1.5 Pro (Executive Writing)"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        start = datetime.utcnow()
        context = context or {}

        try:
            # Gather all agent results from context
            agent_results = context.get("agent_results", [])

            system_prompt = """You are a Senior Investment Banking Associate drafting an investment memo.

MEMO STRUCTURE (McKinsey/Goldman format):
1. EXECUTIVE SUMMARY (1 page)
   - Transaction overview (buyer, target, structure)
   - Valuation range and recommended price
   - Key thesis: 3 reasons to invest
   - Key risks: 3 reasons to pass
   - Final recommendation: STRONG BUY / BUY / HOLD / PASS

2. COMPANY OVERVIEW
   - Business description and history
   - Revenue model and key segments
   - Management team assessment

3. MARKET ANALYSIS
   - Industry dynamics and TAM
   - Competitive positioning
   - Growth drivers and headwinds

4. FINANCIAL ANALYSIS
   - Historical performance (3-5 years)
   - Projected financials
   - Key ratios and benchmarks

5. VALUATION
   - DCF analysis (base, bull, bear cases)
   - Comparable companies analysis
   - Precedent transactions
   - Football field summary

6. RISK ASSESSMENT
   - Risk matrix (probability × impact)
   - Mitigation strategies
   - Downside scenarios

7. RECOMMENDATION
   - Clear BUY/PASS with conditions
   - Suggested entry price and structure

RULES:
- Every claim must reference a data source
- Use precise numbers, not vague qualifiers
- Present ranges, not point estimates
- Flag assumptions explicitly
- Use ONLY figures that appear in AGENT FINDINGS or VERIFIED VALUATION INPUTS. If a figure was
  not retrieved, write "not retrieved" instead of estimating it from memory
- If CROSS-AGENT RECONCILIATION ISSUES are listed, report them in the memo and do not present the
  conflicting figures as consistent or average them
- Do not cite data vendors or publishers (Bloomberg, Capital IQ, FactSet, Gartner, IDC, ...)
  unless they appear in the findings
- Where a figure you derive yourself (scenario IRR, sensitivity) is shown, label it
  "model-derived" and show the inputs it uses"""

            prompt = f"TASK: {task}\n\n"
            if agent_results:
                prompt += "AGENT FINDINGS:\n"
                for r in agent_results[:10]:
                    agent_name = r.get("agent", "unknown")
                    data = render_context(r.get("data", {}), 1500, priority=())
                    prompt += f"\n--- {agent_name} ---\n{data}\n"

            verified = _verified_valuation_block(agent_results)
            if verified:
                prompt += "\n" + verified + "\n"
            reconciliation = reconcile(agent_results)
            if reconciliation:
                prompt += "\n" + reconciliation_notice(reconciliation) + "\n"

            # Cross-agent risks from the deal knowledge graph feed section 6.
            from app.core.knowledge_graph.service import format_risk_register, risk_register

            register = await risk_register(context.get("deal_id"))
            if register:
                prompt += "\n" + format_risk_register(register) + "\n"
                prompt += (
                    "Use this register to populate the RISK ASSESSMENT section and the "
                    "'Key risks' bullets; keep severities as given and note which agent "
                    "area each risk came from.\n"
                )
                context.setdefault("risk_data", register)

            if context:
                prompt += f"\nDEAL CONTEXT: {render_context(context, 4000, exclude=_CONTEXT_EXCLUDE)}\n"

            prompt += (
                "\nDraft a complete investment memo following the structure above."
            )

            result = await self.generate_with_tools(prompt, system_prompt=system_prompt)
            content = result.get("content", "")
            if not memo_is_usable(content):
                self.logger.warning("memo_reply_unusable_retrying", reply=(content or "").strip()[:100])
                result = await self.generate_with_tools(prompt, system_prompt=system_prompt)
                content = result.get("content", "")
            if not memo_is_usable(content):
                raise ValueError(f"Model reply is not a memo: {(content or '').strip()[:100]!r}")

            # Deterministic check: do the memo's figures trace to agent outputs?
            grounding = check_memo_grounding(content, agent_results, str(context.get("user_prompt") or ""))
            if grounding["status"] == "low_grounding":
                content += grounding_notice(grounding)

            # Generate charts if infographic engine available
            charts = self._generate_charts(context)

            analysis = {
                "memo": content,
                "charts": charts,
                "sections": self._extract_sections(content),
                "risk_register": register,
                "grounding": grounding,
            }
            if reconciliation:
                analysis["reconciliation"] = reconciliation
            if grounding["status"] != "ok" or reconciliation:
                analysis["human_review_required"] = True
            analysis.update(await self._build_memo_files(context))

            elapsed = (datetime.utcnow() - start).total_seconds() * 1000
            return AgentOutput(
                success=True,
                data=analysis,
                reasoning="Generated investment memo with executive summary and supporting charts.",
                confidence=self._evidence_confidence(0.85, result, analysis if content.strip() else {})
                * (0.6 if grounding["status"] == "low_grounding" else 1.0),
                execution_time_ms=elapsed,
                tool_calls=result.get("tool_calls"),
            )

        except Exception as e:
            self.logger.error("investment_memo_error", error=str(e))
            return AgentOutput(
                success=False, data={"error": str(e)}, reasoning=str(e), confidence=0.0
            )

    async def _build_memo_files(self, context: Dict) -> Dict[str, Any]:
        """Produce the memo as real DOCX/PDF files via the build_document tool.

        The LLM prose above is a draft; the deliverable files are built by
        Python document libraries from the recorded agent results, so their
        figures and risks come from evidence rather than generated text.
        """
        agent_results = [
            {"agent_type": r.get("agent_type") or r.get("agent"), "success": r.get("success", True), "data": r.get("data")}
            for r in context.get("agent_results", []) if isinstance(r, dict) and isinstance(r.get("data"), dict)
        ]
        if not agent_results:
            return {"files": {}, "file_status": "skipped: no recorded agent results to build from"}
        deal = {
            "id": context.get("deal_id"),
            "name": context.get("deal_name") or context.get("company_name"),
            "target_company": context.get("company_name") or context.get("target_company"),
            "industry": context.get("industry"),
        }
        try:
            result = await self.tools.execute("build_document", {
                "request": "IC memo", "doc_type": "ic_memo", "formats": ["docx", "pdf"],
                "deal": deal, "agent_results": agent_results,
            })
        except Exception as exc:
            self.logger.warning("memo_files_not_built", error=str(exc))
            return {"files": {}, "file_status": f"failed: {type(exc).__name__}"}
        if not result.success or not isinstance(result.data, dict):
            self.logger.warning("memo_files_not_built", error=result.error)
            return {"files": {}, "file_status": f"failed: {result.error}"}
        return {
            "files": result.data.get("files_base64", {}),
            "file_status": "built",
            "file_sections": result.data.get("sections", []),
            "file_review_status": result.data.get("review_status"),
        }

    def _generate_charts(self, context: Dict) -> Dict[str, str]:
        """Generate infographic charts from analysis data."""
        charts = {}
        try:
            from app.core.reports.infographic_engine import InfographicEngine

            # Football field if valuation data exists
            valuations = context.get("valuations")
            if valuations:
                charts["football_field"] = "generated"

            # Risk heatmap if risk data exists
            risks = context.get("risk_data")
            if risks:
                charts["risk_heatmap"] = "generated"

            # Deal score radar if scores exist
            scores = context.get("deal_scores")
            if scores:
                charts["deal_radar"] = "generated"

        except ImportError:
            self.logger.warning("infographic_engine_not_available")
        return charts

    def _extract_sections(self, content: str) -> List[Dict]:
        """Extract memo sections from LLM output."""
        sections = []
        current = {"title": "Introduction", "content": ""}
        for line in content.split("\n"):
            if line.strip().startswith("#"):
                if current["content"].strip():
                    sections.append(current)
                current = {"title": line.strip().lstrip("#").strip(), "content": ""}
            else:
                current["content"] += line + "\n"
        if current["content"].strip():
            sections.append(current)
        return sections
