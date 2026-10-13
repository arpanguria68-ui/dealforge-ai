"""
DCF & LBO Modeling Agent — 'The Architect'

Automates financial spreading, LBO debt waterfalls, and DCF models.
Uses deterministic calculations (0% arithmetic error tolerance) per PRD.
"""

from typing import Dict, Any, Optional, List
import json
import math
from datetime import datetime

from app.agents.base import BaseAgent, AgentOutput
from app.core.prompt_context import render_context


class DCFLBOArchitectAgent(BaseAgent):
    """
    The Architect — builds and executes financial models:
    - 3-Statement financial spreading
    - DCF with mid-year convention
    - LBO debt waterfall and cash flow sweep
    - Returns waterfall (IRR, MOIC, DPI)
    """

    name = "dcf_lbo_architect"
    description: str = "Automates DCF models, LBO debt waterfalls, financial spreading, and returns analysis"
    recommended_model: str = "Gemini 1.5 Pro (LBO/DCF Precision)"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        start = datetime.utcnow()
        context = context or {}
        result = {}

        try:
            # Retrieve any relevant knowledge from RAG
            memory_context = []
            if self.pageindex_client:
                # Retrieve general context
                memory_context = await self.retrieve_context(
                    f"LBO DCF debt waterfall financial model {task}", top_k=5
                )
                # Retrieve specific risk documents if a fact_base is present
                risk_docs = []
                if context.get("fact_base"):
                    company_name = context["fact_base"].get("company_name", "")
                    if company_name:
                        risk_docs = await self.retrieve_context(
                            f"{company_name} risk assessment", top_k=3
                        )
                    memory_context.extend(risk_docs) # Add risk docs to memory context

            # Build analysis prompt
            system_prompt = self._build_system_prompt()
            prompt = self._build_prompt(task, context, memory_context)

            # Generate with tools
            result = await self.generate_with_tools(prompt, system_prompt=system_prompt)
            content = result.get("content", "")

            # Run deterministic calculations if financial data available
            calc_results = {}
            # Priority: Use structured FactBase if available
            fact_base = context.get("fact_base", {})
            fin_data = fact_base.get("metrics") or context.get("financial_data", {})
            
            if fin_data:
                calc_results = self._run_calculations(fin_data)

            # Parse LLM analysis
            analysis = self._parse_output(content)
            analysis["deterministic_calculations"] = calc_results
            self._ensure_lbo(task, context, analysis)
            self._repair_dcf_bridge(context, analysis)

            elapsed = (datetime.utcnow() - start).total_seconds() * 1000
            return AgentOutput(
                success=True,
                data=analysis,
                reasoning=f"Built financial model covering DCF/LBO analysis. "
                + (f"Deterministic calculations: {len(calc_results)} cross-checked."
                   if calc_results else
                   "No deterministic cross-check ran (no structured financial inputs); figures are model-stated."),
                confidence=self._evidence_confidence(0.85, result, analysis),
                execution_time_ms=elapsed,
                tool_calls=result.get("tool_calls"),
            )

        except Exception as e:
            self.logger.error("dcf_lbo_error", error=str(e))
            return AgentOutput(
                success=False, data={"error": str(e)}, reasoning=str(e), confidence=0.0
            )

    def _build_system_prompt(self) -> str:
        return """You are a Senior Financial Modeler at a top-tier investment bank.
Your role is to build and analyze financial models with zero arithmetic errors.

CAPABILITIES:
- 3-Statement Model: Link IS, BS, CF dynamically
- DCF: Unlevered FCF → WACC → Terminal Value → Enterprise Value
- LBO: Entry → Debt Waterfall → Cash Sweep → Exit Returns
- Sensitivity: Data tables on key assumptions (WACC, growth, multiples)

RULES:
1. Every number must be traceable to a source or calculation
2. Show your work: present formulas and intermediate steps
3. Flag any assumptions explicitly
4. Use mid-year convention for DCF by default
5. Always present results in a structured format"""

    def _build_prompt(self, task: str, context: Dict, memory: list) -> str:
        prompt = f"TASK: {task}\n\n"
        if context:
            prompt += f"DEAL CONTEXT:\n{render_context(context)}\n\n"
            if context.get("fact_base"):
                prompt += f"FACT BASE (GROUND TRUTH):\n{json.dumps(context['fact_base'], default=str)}\n\n"
        if memory:
            prompt += f"RELEVANT KNOWLEDGE:\n{chr(10).join(str(m)[:200] for m in memory[:3])}\n\n"
        prompt += "Provide your analysis in structured JSON format with sections for each model built."
        return prompt

    def _ensure_lbo(self, task: str, context: Dict, analysis: Dict) -> None:
        """Add a deterministic LBO when one was requested but the model's output has none."""
        import re

        wanted = f"{task} {context.get('user_prompt', '')}"
        if not re.search(r"\blbo\b|leveraged\s+buy-?out|take[- ]private", wanted, re.IGNORECASE):
            return
        if any("lbo" in str(key).lower() for key in analysis):
            return
        from app.core.lbo import build_lbo
        from app.core.valuation_inputs import DEFAULT_ASSUMPTIONS

        for record in context.get("agent_results") or []:
            data = record.get("data") if isinstance(record, dict) else None
            audit = data.get("audit") if isinstance(data, dict) and isinstance(data.get("audit"), dict) else None
            inputs = (audit or {}).get("inputs") or {}
            fin, market = inputs.get("financial_data") or {}, inputs.get("market_data") or {}
            lbo = build_lbo(
                market.get("enterprise_value"), fin.get("free_cash_flow"), fin.get("revenue_growth"),
                DEFAULT_ASSUMPTIONS["terminal_growth"],
            )
            if lbo:
                analysis["lbo_model"] = lbo
                return
        limits = analysis.setdefault("data_limitations", [])
        if isinstance(limits, list):
            limits.append("An LBO was requested but could not be built: no sourced market enterprise value or free cash flow.")

    @staticmethod
    def _valuation_inputs(context: Dict) -> tuple:
        """(financial_data, market_data) the valuation agent sourced, from upstream results."""
        for record in context.get("agent_results") or []:
            data = record.get("data") if isinstance(record, dict) else None
            audit = data.get("audit") if isinstance(data, dict) and isinstance(data.get("audit"), dict) else None
            inputs = (audit or {}).get("inputs") or {}
            if inputs.get("financial_data"):
                return inputs.get("financial_data") or {}, inputs.get("market_data") or {}
        return {}, {}

    def _repair_dcf_bridge(self, context: Dict, analysis: Dict) -> None:
        """Replace a DCF bridge whose parts do not sum to its enterprise value with a computed one."""
        from app.core.lbo import deterministic_dcf
        from app.core.valuation_inputs import DEFAULT_ASSUMPTIONS
        from app.core.validation.reconciliation import BRIDGE_TOLERANCE, declared_scale, parse_money

        fin, _ = self._valuation_inputs(context)
        scale = declared_scale(analysis)

        def parsed(node: Dict, *needles: str) -> Optional[float]:
            return next((parse_money(v, k, scale) for k, v in node.items()
                         if all(n in k.lower() for n in needles)), None)

        def walk(node: Any) -> bool:
            items = list(node.items()) if isinstance(node, dict) else list(enumerate(node)) if isinstance(node, list) else []
            for key, child in items:
                if isinstance(child, dict) and "bridge" in str(key).lower():
                    pv_cash = parsed(child, "pv", "fcf") or parsed(child, "pv", "cash")
                    pv_terminal, ev = parsed(child, "pv", "terminal"), parsed(child, "enterprise")
                    if pv_cash and pv_terminal and ev and abs(pv_cash + pv_terminal - ev) / ev > BRIDGE_TOLERANCE:
                        rebuilt = deterministic_dcf(
                            fin.get("free_cash_flow"), fin.get("revenue_growth"), fin.get("wacc"),
                            DEFAULT_ASSUMPTIONS["terminal_growth"], int(DEFAULT_ASSUMPTIONS["projection_years"]),
                        )
                        if rebuilt:
                            node[key] = rebuilt
                            return True
                elif walk(child):
                    return True
            return False

        if walk(analysis):
            limits = analysis.setdefault("data_limitations", [])
            if isinstance(limits, list):
                limits.append("The model's DCF bridge did not add up; it was recomputed from the shared valuation assumptions.")

    def _parse_output(self, content: str) -> Dict:
        from app.core.json_helpers import extract_and_parse_json

        parsed = extract_and_parse_json(content)
        if parsed:
            return parsed
        return {"analysis": content, "format": "narrative"}

    def _run_calculations(self, fin_data: Dict) -> Dict:
        """Run deterministic financial calculations."""
        results = {}

        # DCF if projected cash flows available
        fcf = fin_data.get("projected_fcf", [])
        wacc = fin_data.get("wacc", 0.10)
        if fcf:
            results["dcf"] = self._calculate_dcf(fcf, wacc)

        # LBO if entry/exit data available
        if fin_data.get("entry_ev") and fin_data.get("exit_ev"):
            results["lbo_returns"] = self._calculate_lbo(fin_data)

        # Debt waterfall
        if fin_data.get("debt_tranches"):
            results["debt_waterfall"] = self._calculate_debt_waterfall(fin_data)

        return results

    @staticmethod
    def _calculate_dcf(
        fcf: List[float], wacc: float, terminal_growth: float = 0.025
    ) -> Dict:
        """Deterministic DCF with mid-year convention."""
        pv_fcf = []
        for i, cf in enumerate(fcf):
            discount = (1 + wacc) ** (i + 0.5)  # mid-year
            pv_fcf.append(round(cf / discount, 2))

        terminal_fcf = fcf[-1] * (1 + terminal_growth)
        terminal_value = terminal_fcf / (wacc - terminal_growth)
        pv_terminal = terminal_value / ((1 + wacc) ** len(fcf))

        return {
            "pv_of_fcf": round(sum(pv_fcf), 2),
            "terminal_value": round(terminal_value, 2),
            "pv_of_terminal": round(pv_terminal, 2),
            "enterprise_value": round(sum(pv_fcf) + pv_terminal, 2),
            "yearly_pv": pv_fcf,
            "assumptions": {
                "wacc": wacc,
                "terminal_growth": terminal_growth,
                "convention": "mid-year",
            },
        }

    @staticmethod
    def _calculate_lbo(data: Dict) -> Dict:
        """LBO returns: IRR and MOIC."""
        entry_ev = data["entry_ev"]
        exit_ev = data["exit_ev"]
        equity_pct = data.get("equity_pct", 0.4)
        holding = data.get("holding_years", 5)
        debt_paydown = data.get("debt_paydown", 0)

        equity_in = entry_ev * equity_pct
        exit_equity = exit_ev - (entry_ev * (1 - equity_pct) - debt_paydown)
        moic = exit_equity / equity_in if equity_in > 0 else 0
        irr = (moic ** (1 / holding) - 1) if moic > 0 and holding > 0 else 0

        return {
            "equity_invested": round(equity_in, 2),
            "exit_equity_value": round(exit_equity, 2),
            "moic": round(moic, 2),
            "irr": round(irr * 100, 2),
            "holding_period": holding,
        }

    @staticmethod
    def _calculate_debt_waterfall(data: Dict) -> Dict:
        """Calculate debt paydown waterfall with cash sweep."""
        tranches = data[
            "debt_tranches"
        ]  # [{"name": "Revolver", "amount": 50, "rate": 0.05}, ...]
        annual_cf = data.get("annual_free_cash_flow", 0)
        sweep_pct = data.get("sweep_percentage", 0.75)
        years = data.get("holding_years", 5)

        schedule = []
        remaining = {t["name"]: t["amount"] for t in tranches}

        for year in range(1, years + 1):
            available = annual_cf * sweep_pct
            year_payments = {}
            for tranche in tranches:
                name = tranche["name"]
                if remaining[name] > 0:
                    payment = min(available, remaining[name])
                    year_payments[name] = round(payment, 2)
                    remaining[name] -= payment
                    available -= payment
            schedule.append(
                {"year": year, "payments": year_payments, "remaining": dict(remaining)}
            )

        return {"schedule": schedule, "total_remaining": sum(remaining.values())}
