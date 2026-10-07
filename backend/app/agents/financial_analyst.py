"""Financial Analyst Agent"""

from typing import Dict, Any, Optional, List
import asyncio
import json
import math
import re
from datetime import datetime

from app.agents.base import BaseAgent, AgentOutput
from app.core.scoring.deal_scorer import DealScorer


class FinancialAnalystAgent(BaseAgent):
    """
    Agent for financial analysis and valuation

    Responsibilities:
    - Financial statement analysis
    - Valuation modeling (DCF, multiples)
    - Financial risk assessment
    - Return projections
    """

    name = "financial_analyst"
    description: str = "Analyzes financial performance and creates valuation models"
    recommended_model: str = "Gemini 1.5 Pro (Large Context Finance)"

    @staticmethod
    def is_input_only_request(task: str) -> bool:
        text = task or ""
        explicit_no_sources = re.search(
            r"\b(?:no|without|do not use|don't use|do not fetch|don't fetch)\s+"
            r"(?:any\s+)?(?:external\s+(?:data|apis?|sources?)|web(?:\s+search)?|"
            r"third[- ]party\s+(?:data|apis?)|mcp)\b",
            text,
            re.IGNORECASE,
        )
        explicit_no_source_list = re.search(
            r"\bno\s+(?:(?:cloud\s+llm|web(?:\s+search)?|external\s+(?:data|apis?|sources?)|mcp|fallback)\s*,\s*){1,5}"
            r"(?:(?:and|or)\s+)?(?:cloud\s+llm|web(?:\s+search)?|external\s+(?:data|apis?|sources?)|mcp|fallback)\b",
            text,
            re.IGNORECASE,
        )
        explicitly_offline = re.search(
            r"\bdo not (?:fetch|use|search for) external data\b|"
            r"\buse only (?:the )?(?:supplied|provided|user[- ]provided) (?:data|figures|metrics|inputs)\b",
            text,
            re.IGNORECASE,
        )
        if not explicitly_offline and re.search(
            r"\b(?:if available.{0,100}(?:fetch|retrieve|look up)|"
            r"(?:retrieve|look up).{0,100}\b(?:api|public peer)|"
            r"use (?:the )?(?:financial )?api)\b",
            text,
            re.IGNORECASE | re.DOTALL,
        ):
            return False
        return bool(explicit_no_sources or explicit_no_source_list or re.search(
            r"\bdo not (?:fetch|use|search for) external data\b|"
            r"\b(?:use|rely on) only (?:(?:the|these) )?(?:(?:user[- ]provided|provided|supplied) )?(?:figures|data|metrics|inputs|information|facts)\b|"
            r"\bdo not (?:infer|invent|estimate) (?:any )?(?:missing|unsupported) (?:facts|figures|data|values)\b",
            task,
            re.IGNORECASE,
        ))

    @staticmethod
    def _requested_fiscal_year_count(task: str) -> Optional[int]:
        match = re.search(
            r"\b(?:latest|last|past)\s+(two|three|four|five|six|[2-6])\s+"
            r"(?:[a-zA-Z0-9-]+\s+){0,3}(?:fiscal years?|years?|periods?|filings?)\b",
            task or "",
            re.IGNORECASE,
        )
        if not match:
            return None
        return {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}.get(
            match.group(1).lower(), int(match.group(1)) if match.group(1).isdigit() else None
        )

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        """
        Execute financial analysis task

        Args:
            task: Analysis task description
            context: Deal context with financial data

        Returns:
            AgentOutput with financial analysis
        """
        start_time = datetime.now()
        self.logger.info("Starting financial analysis", task=task)

        if self.is_input_only_request(task):
            grounded_assessment = self._provided_metrics_assessment(task)
            if grounded_assessment:
                await self._apply_laya_output_gate(task, grounded_assessment)
                return AgentOutput(
                    success=True,
                    data=grounded_assessment,
                    reasoning=grounded_assessment["reasoning"],
                    confidence=0.35,
                    execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                )
            return AgentOutput(
                success=False,
                data={"error": "input_metrics_not_found", "data_limitations": [
                    "The request forbids external data, but no supported financial metrics could be extracted."
                ]},
                reasoning=(
                    "This is an input-only request. I could not extract supported financial metrics, "
                    "so I stopped without calling an external LLM or inventing values."
                ),
                confidence=0.0,
                execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
            )

        # Retrieve relevant financial documents
        deal_id = context.get("deal_id") if context else None

        prefetched_tool_results = []
        requested_period_count = self._requested_fiscal_year_count(task)
        identifier = (context or {}).get("ticker") or (context or {}).get("company_name")
        if identifier:
            try:
                from app.core.tools.financial_data_api import FetchFinancialStatementsTool

                fetched = await asyncio.wait_for(
                    FetchFinancialStatementsTool().execute(
                        ticker=str(identifier), statements=["income", "balance", "cashflow"],
                        periods=requested_period_count or 5, frequency="annual",
                    ),
                    timeout=45,
                )
                if fetched.success and isinstance(fetched.data, dict):
                    context = {**(context or {}), "financial_data": fetched.data}
                    prefetched_tool_results.append({
                        "name": "fetch_financial_statements",
                        "success": True,
                        "data": fetched.data,
                    })
                else:
                    context = {
                        **(context or {}),
                        "financial_data_retrieval": {
                            "status": "unavailable",
                            "identifier": str(identifier),
                            "reason": fetched.error or "No filing data returned",
                        },
                    }
            except Exception as exc:
                self.logger.warning("financial_data_prefetch_failed", error=str(exc))
                context = {
                    **(context or {}),
                    "financial_data_retrieval": {
                        "status": "error", "identifier": str(identifier),
                        "reason": type(exc).__name__,
                    },
                }

        fetched_financial_data = (context or {}).get("financial_data")
        if (
            isinstance(fetched_financial_data, dict)
            and fetched_financial_data.get("has_data")
            and re.search(r"\b(?:report|show|fetch|retrieve)\b", task, re.IGNORECASE)
            and re.search(r"\b(?:revenue|financial statements)\b", task, re.IGNORECASE)
            and not re.search(
                r"\b(?:investment thesis|recommendation|risk assessment|quality of earnings|market analysis|valuation analysis|DCF|LBO)\b",
                task,
                re.IGNORECASE,
            )
        ):
            assessment = self._statement_data_assessment(
                context["financial_data"], period_limit=requested_period_count
            )
            if not assessment.get("historical_financials"):
                return AgentOutput(
                    success=False,
                    data={"error": "no_standardized_financial_metrics", **assessment},
                    reasoning="The provider returned data, but no supported standardized financial metrics were extracted.",
                    confidence=0.0,
                    execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                )
            assessment["synthesis_status"] = "deterministic_source_report"
            assessment["calculations"] = prefetched_tool_results
            return AgentOutput(
                success=True,
                data=assessment,
                reasoning=assessment["reasoning"],
                confidence=0.65,
                execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
            )

        # Build context from memory
        memory_context = []
        if deal_id:
            memory_context = await self.retrieve_context(
                f"financial data revenue EBITDA cash flow {deal_id}", top_k=5
            )

        # RL Loop: Inject historically successful patterns
        from app.core.quality.agent_quality_store import AgentQualityStore

        quality_store = AgentQualityStore()
        await quality_store.initialize()
        best_practices = await quality_store.get_historical_best_practices(
            self.name, "deal_analysis"
        )

        # Build analysis prompt
        prompt = self._build_analysis_prompt(task, context, memory_context)
        system_prompt = self._build_system_prompt(best_practices)

        # Generate analysis with tools
        try:
            response = await self.generate_with_tools(prompt, system_prompt, json_mode=True)
        except Exception as exc:
            response = {
                "error": "provider_generation_failed",
                "content": f"[Provider unavailable] {type(exc).__name__}: {exc}",
            }

        if response.get("error") or str(response.get("content", "")).lstrip().startswith(
            ("[Error]", "[Rate limited]", "[Provider unavailable]")
        ):
            sourced_financials = (context or {}).get("financial_data")
            if isinstance(sourced_financials, dict) and sourced_financials.get("has_data"):
                assessment = self._statement_data_assessment(
                    sourced_financials, period_limit=requested_period_count
                )
                if not assessment.get("historical_financials"):
                    return AgentOutput(
                        success=False,
                        data={"error": "no_standardized_financial_metrics", **assessment},
                        reasoning="The provider returned data, but no supported standardized financial metrics were extracted.",
                        confidence=0.0,
                        execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                    )
                assessment["synthesis_status"] = "partial_provider_unavailable"
                assessment["provider_warning"] = str(
                    response.get("content") or "No synthesis provider returned a usable response."
                )
                return AgentOutput(
                    success=True,
                    data=assessment,
                    reasoning=assessment["reasoning"],
                    confidence=0.45,
                    execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                )
            return AgentOutput(
                success=False,
                data={
                    "error": response.get("error", "provider_generation_failed"),
                    "financial_data": (context or {}).get("financial_data"),
                    "data_retrieval": (context or {}).get("financial_data_retrieval"),
                },
                reasoning=str(response.get("content") or "LLM provider failed without a usable response."),
                confidence=0.0,
                execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
            )

        # Parse and structure output
        try:
            content = response.get("content", "") or ""
            analysis_data = self._parse_analysis_output(content)
            source_grounded_fallback = False

            known_sections = {
                "revenue_analysis", "profitability", "cash_flow", "valuation",
                "financial_risks", "investment_thesis", "recommendation",
            }
            if not isinstance(analysis_data, dict) or not known_sections.intersection(analysis_data):
                sourced_financials = (context or {}).get("financial_data")
                if isinstance(sourced_financials, dict) and sourced_financials.get("has_data"):
                    analysis_data = self._statement_data_assessment(
                        sourced_financials, period_limit=requested_period_count
                    )
                    if not analysis_data.get("historical_financials"):
                        return AgentOutput(
                            success=False,
                            data={"error": "no_standardized_financial_metrics", **analysis_data},
                            reasoning="The provider returned data, but no supported standardized financial metrics were extracted.",
                            confidence=0.0,
                            execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                        )
                    source_grounded_fallback = True
                    analysis_data["synthesis_status"] = "source_grounded_fallback"
                    analysis_data["provider_warning"] = (
                        "The synthesis model did not return the required structured financial sections; "
                        "reported data and derived calculations below come from SEC filing facts."
                    )
                else:
                    analysis_data = self._provided_metrics_assessment(task)
                    if not analysis_data:
                        return AgentOutput(
                            success=False,
                            data={"error": "unstructured_model_output"},
                            reasoning="The model did not return structured financial analysis, and no verified source data or extractable supplied metrics were available.",
                            confidence=0.0,
                            execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                        )

            # Add tool results if available
            tool_results = prefetched_tool_results + response.get("tool_results", [])
            if tool_results:
                analysis_data["calculations"] = tool_results

            if not source_grounded_fallback:
                self._enforce_evidence_grounding(analysis_data, task, context or {}, tool_results)

            await self._apply_laya_output_gate(task, analysis_data)

            # Calculate confidence
            confidence = self._calculate_confidence(analysis_data)

            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            return AgentOutput(
                success=True,
                data=analysis_data,
                reasoning=analysis_data.get("reasoning", ""),
                confidence=confidence,
                execution_time_ms=execution_time,
                tool_calls=response.get("function_calls"),
            )

        except Exception as e:
            self.logger.error("Failed to parse analysis", error=str(e))
            return AgentOutput(
                success=False,
                data={},
                reasoning=f"Analysis failed: {str(e)}",
                confidence=0.0,
            )

    async def _apply_laya_output_gate(self, task: str, analysis: Dict[str, Any]) -> None:
        """Use Laya as an advisory unsupported-claim gate, never as an arithmetic oracle."""
        try:
            from app.core.laya.client import get_laya_client

            client = get_laya_client()
            # Financial inputs can be sensitive. Do not route them to a remote
            # classifier or cause the local checkpoint backend to download weights.
            if client.backend != "lmstudio":
                return

            evidence = {
                "task": task[:2500],
                "fact_ledger": {
                    key: analysis.get(key)
                    for key in (
                        "historical_financials", "revenue_analysis", "profitability",
                        "balance_sheet", "valuation", "derived_metrics", "sources",
                    )
                    if analysis.get(key) is not None
                },
                "candidate_analysis": str(analysis.get("reasoning") or "")[:2500],
            }
            gate = await asyncio.wait_for(
                client.gate_confidence(json.dumps(evidence, default=str)), timeout=15
            )
            if gate is None:
                return
            flagged = float(gate.get("red_flag_p", 0) or 0) >= 0.5 or float(
                gate.get("supported_p", 0.5) or 0
            ) < 0.5
            analysis["laya_quality_gate"] = {
                **gate,
                "decision": "human_review" if flagged else "no_flag",
                "scope": "advisory; deterministic fact and calculation checks remain authoritative",
            }
            if flagged:
                analysis["human_review_required"] = True
        except Exception as exc:
            self.logger.warning("laya_financial_output_gate_failed", error=type(exc).__name__)


    def _build_analysis_prompt(
        self, task: str, context: Optional[Dict], memory_context: list
    ) -> str:
        """Build the analysis prompt"""
        context_str = (
            json.dumps(context, indent=2) if context else "No additional context"
        )
        memory_str = (
            "\n".join([f"- {m['content'][:200]}..." for m in memory_context])
            if memory_context
            else "No retrieved documents"
        )

        return f"""Task: {task}

Context:
{context_str}

FACT BASE (GROUND TRUTH):
{json.dumps(context.get('fact_base', {}), indent=2) if context and context.get('fact_base') else "No FactBase available yet."}

Relevant Documents:
{memory_str}

Provide a financial analysis limited to what the input and returned tools establish. Use concise conclusions rather than filling every section with guesses:
1. Revenue analysis (growth trends, quality, concentration)
2. Profitability metrics (margins, trends)
3. Cash flow analysis
4. Balance sheet strength
5. Valuation estimate (DCF and multiples)
6. Key financial risks
7. Investment thesis from financial perspective

Respond with structured JSON:
{{
    "revenue_analysis": {{
        "annual_revenue": number,
        "growth_rate": number,
        "quality_assessment": string
    }},
    "profitability": {{
        "gross_margin": number,
        "ebitda_margin": number,
        "trend": string
    }},
    "cash_flow": {{
        "operating_cash_flow": number or null,
        "free_cash_flow": number or null,
        "burn_rate": number or null
    }},
    "valuation": {{
        "dcf_estimate": number or null,
        "multiple_estimate": number or null,
        "confidence_range": {{"low": number or null, "high": number or null}}
    }},
    "financial_risks": [string],
    "investment_thesis": string,
    "reasoning": string,
    "recommendation": "proceed" | "caution" | "reject"
}}"""

    def _build_system_prompt(self, best_practices: List[str] = None) -> str:
        """Build system prompt for financial analysis"""
        prompt = f"""You are {self.name}, {self.description}.

You are an expert financial analyst with deep experience in:
- Financial modeling and valuation
- M&A transaction analysis
- Due diligence
- Investment thesis development

Guidelines:
- Treat user-provided metrics as authoritative inputs, not independently verified facts. Never replace them with conflicting tool data; report discrepancies separately with source and date.
- Never infer operating cash flow, free cash flow, burn, debt terms, forecasts, or valuation from revenue, EBITDA, cash, or debt alone. Return null when not established.
- Use web and API tools when the task requests external research, verification, comparables, or current information. For private companies, report unavailable data and continue with sourced inputs.
- For public-company research, search first, then fetch the primary annual report or filing with `web_scraper`; search snippets alone are leads, not verified financial evidence.
- If you need historical financials, use `fetch_financial_statements` or `financial_datasets`.
- If you need real-time stock/market data, use `finnhub_data` or `alpha_vantage`.
- Always show your calculation methodology.
- For every derived or externally sourced number, identify the source/tool and period. Do not treat a model estimate as a sourced fact.
- Missing or failed tool results are unknown, never zero. Clearly separate supplied, externally sourced, calculated, and estimated values.
- Format all currency values consistently.
"""
        if best_practices:
            prompt += (
                "\n\nHistorical Best Practices (Learn from past high-scoring deals):\n"
            )
            for bp in best_practices:
                prompt += f"- {bp}\n"

        return prompt

    def _parse_analysis_output(self, content: str) -> Dict[str, Any]:
        """Parse analysis output from LLM response"""
        from app.core.json_helpers import extract_and_parse_json

        return extract_and_parse_json(content)

    def _enforce_evidence_grounding(
        self, analysis: Dict[str, Any], task: str, context: Dict[str, Any], tool_results: list
    ) -> None:
        """Discard model-filled fields unless the input or a successful data tool supports them."""
        normalized_task = task.lower()
        financial_data_tools = {
            "financial_datasets", "fetch_financial_statements", "finance_analysis",
            "alpha_vantage", "finnhub_data", "company_data", "sec_filings",
        }
        valuation_tools = {
            "financial_calculator", "fetch_comparable_companies",
            "generate_football_field", "run_sensitivity_analysis", "run_monte_carlo_irr",
        }

        def contains_numeric_field(payload: Any, names: tuple) -> bool:
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if str(key).lower() in names and isinstance(value, (int, float)) and not isinstance(value, bool):
                        return True
                    if contains_numeric_field(value, names):
                        return True
            elif isinstance(payload, list):
                return any(contains_numeric_field(item, names) for item in payload)
            return False

        def task_has_amount(labels: str) -> bool:
            return bool(re.search(
                rf"(?:[-+]?\$?\s*\d[\d,.]*\s*(?:m|million|b|billion)?\s*(?:{labels})|"
                rf"(?:{labels})\s*(?:of\s*)?[:=]?\s*[-+]?\$?\s*\d[\d,.]*\s*(?:m|million|b|billion)?)",
                normalized_task,
                re.IGNORECASE,
            ))

        def input_number(*names: str) -> Optional[float]:
            wanted = {name.lower() for name in names}

            def find(payload: Any) -> Optional[float]:
                if isinstance(payload, dict):
                    for key, value in payload.items():
                        if str(key).lower() in wanted:
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                return float(value)
                            if isinstance(value, dict):
                                points = [(str(period), amount) for period, amount in value.items()
                                          if period != "_sources" and isinstance(amount, (int, float))]
                                if points:
                                    return float(sorted(points)[-1][1])
                        found = find(value)
                        if found is not None:
                            return found
                elif isinstance(payload, list):
                    for value in payload:
                        found = find(value)
                        if found is not None:
                            return found
                return None

            return find(context)

        def sourced_number(*names: str) -> Optional[float]:
            for item in tool_results:
                if not isinstance(item, dict) or not item.get("success"):
                    continue
                if item.get("name", "").lower() not in financial_data_tools:
                    continue
                wanted = {name.lower() for name in names}

                def find(payload: Any) -> Optional[float]:
                    if isinstance(payload, dict):
                        for key, candidate in payload.items():
                            if str(key).lower() in wanted:
                                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                                    return float(candidate)
                                if isinstance(candidate, dict):
                                    points = [(str(period), amount) for period, amount in candidate.items()
                                              if period != "_sources" and isinstance(amount, (int, float))]
                                    if points:
                                        return float(sorted(points)[-1][1])
                            found = find(candidate)
                            if found is not None:
                                return found
                    elif isinstance(payload, list):
                        for candidate in payload:
                            found = find(candidate)
                            if found is not None:
                                return found
                    return None

                found = find(item.get("data"))
                if found is not None:
                    return found
            return None

        def numeric_series(payload: Any, names: tuple) -> Dict[str, float]:
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if str(key).lower() in names and isinstance(value, dict):
                        series = {
                            str(period): float(amount)
                            for period, amount in value.items()
                            if period != "_sources" and isinstance(amount, (int, float))
                        }
                        if series:
                            return series
                for value in payload.values():
                    found = numeric_series(value, names)
                    if found:
                        return found
            elif isinstance(payload, list):
                for value in payload:
                    found = numeric_series(value, names)
                    if found:
                        return found
            return {}

        def sourced_series(*names: str) -> Dict[str, float]:
            wanted = tuple(name.lower() for name in names)
            series = numeric_series(context.get("financial_data", {}), wanted)
            if series:
                return series
            for item in tool_results:
                if (
                    isinstance(item, dict) and item.get("success")
                    and str(item.get("name", "")).lower() in financial_data_tools
                ):
                    series = numeric_series(item.get("data"), wanted)
                    if series:
                        return series
            return {}

        scraped_sources = []
        scraped_revenue = None
        scraped_growth = None
        scale = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000, "trillion": 1_000_000_000_000}
        for item in tool_results:
            if not isinstance(item, dict) or not item.get("success") or item.get("name", "").lower() != "web_scraper":
                continue
            page = item.get("data") or {}
            text = str(page.get("text", ""))
            if not text:
                continue
            year_match = re.search(r"20\d{2}", task, re.IGNORECASE)
            revenue_match = re.search(
                r"\b(?:total\s+)?revenue\s+(?:was|of|totaled|reached)\s+\$?\s*([\d,.]+)\s*(thousand|million|billion|trillion)\b",
                text,
                re.IGNORECASE,
            )
            if revenue_match:
                scraped_revenue = float(revenue_match.group(1).replace(",", "")) * scale[revenue_match.group(2).lower()]
                sentence = next(
                    (part for part in re.split(r"(?<=[.!?])\s+", text)
                     if re.search(r"\brevenue\b", part, re.IGNORECASE)
                     and revenue_match.group(0).lower() in part.lower()),
                    "",
                )
                growth_match = re.search(r"\b(?:up|grew|increased)\s+([\d,.]+)\s*percent\b", sentence, re.IGNORECASE)
                if growth_match:
                    scraped_growth = float(growth_match.group(1).replace(",", ""))
            scraped_sources.append({
                "title": page.get("title") or "Web page",
                "url": page.get("url"),
                "source_type": "web_scraper",
                "period": f"FY{year_match.group(0)}" if year_match else None,
            })

        provided = self._provided_metrics_assessment(task)
        metric_conflicts = []

        def note_conflict(label: str, claimed: Any, calculated: Any) -> None:
            if (
                isinstance(claimed, (int, float))
                and not isinstance(claimed, bool)
                and isinstance(calculated, (int, float))
                and not isinstance(calculated, bool)
                and not math.isclose(float(claimed), float(calculated), rel_tol=0.01, abs_tol=0.02)
            ):
                metric_conflicts.append({
                    "metric": label,
                    "model_value": claimed,
                    "validated_value": calculated,
                    "resolution": "Structured output uses the deterministic value; review related narrative before release.",
                })

        claimed_revenue = analysis.get("revenue_analysis") or {}
        claimed_profitability = analysis.get("profitability") or {}
        claimed_balance = analysis.get("balance_sheet") or {}
        claimed_valuation = analysis.get("valuation") or {}
        for label, claimed, validated in (
            ("revenue_growth_rate", claimed_revenue.get("growth_rate"), provided.get("revenue_analysis", {}).get("growth_rate")),
            ("gross_margin", claimed_profitability.get("gross_margin"), provided.get("profitability", {}).get("gross_margin")),
            ("ebitda_margin", claimed_profitability.get("ebitda_margin"), provided.get("profitability", {}).get("ebitda_margin")),
            ("net_debt", claimed_balance.get("net_debt"), provided.get("balance_sheet", {}).get("net_debt")),
            ("net_debt_to_ebitda", claimed_balance.get("net_debt_to_ebitda"), provided.get("balance_sheet", {}).get("net_debt_to_ebitda")),
            ("ev_ebitda_multiple", claimed_valuation.get("ev_ebitda_multiple", claimed_valuation.get("multiple_estimate")), provided.get("valuation", {}).get("ev_ebitda_multiple")),
            ("equity_value", claimed_valuation.get("equity_value"), provided.get("valuation", {}).get("equity_value")),
        ):
            note_conflict(label, claimed, validated)
        revenue = input_number("revenue", "annual_revenue", "annualRevenue")
        if revenue is None:
            revenue = provided.get("revenue_analysis", {}).get("annual_revenue")
        if revenue is None:
            revenue = sourced_number("revenue", "annual_revenue", "annualRevenue")
        if revenue is None:
            revenue = scraped_revenue
        gross_profit = input_number("gross_profit", "grossProfit")
        if gross_profit is None:
            gross_profit = provided.get("profitability", {}).get("gross_profit")
        if gross_profit is None:
            gross_profit = sourced_number("gross_profit", "grossProfit")
        ebitda = input_number("ebitda")
        if ebitda is None:
            ebitda = provided.get("profitability", {}).get("ebitda")
        if ebitda is None:
            ebitda = sourced_number("ebitda", "ebitda_usd")
        operating_income = input_number("operating_income", "operatingIncome")
        if operating_income is None:
            operating_income = sourced_number("operating_income", "operatingIncome")
        net_income = input_number("net_income", "netIncome")
        if net_income is None:
            net_income = sourced_number("net_income", "netIncome")
        growth = provided.get("revenue_analysis", {}).get("growth_rate")
        if growth is None:
            growth = input_number("revenue_cagr", "cagr", "growth_rate", "revenue_growth")
        if growth is None:
            growth = sourced_number("revenue_cagr", "cagr", "growth_rate", "revenue_growth")
        if growth is None:
            growth = scraped_growth
        if growth is not None and -1 <= growth <= 1:
            growth *= 100

        revenue_analysis = analysis.get("revenue_analysis")
        if isinstance(revenue_analysis, dict):
            revenue_analysis["annual_revenue"] = revenue
            revenue_analysis["growth_rate"] = growth
            if scraped_sources:
                analysis["sources"] = scraped_sources

        profitability = analysis.get("profitability")
        if isinstance(profitability, dict):
            profitability["gross_profit"] = gross_profit
            profitability["ebitda"] = ebitda
            if "operating_income" in profitability:
                profitability["operating_income"] = operating_income
            if "net_income" in profitability:
                profitability["net_income"] = net_income
            profitability["gross_margin"] = (
                round(gross_profit / revenue * 100, 2)
                if gross_profit is not None and revenue
                else provided.get("profitability", {}).get("gross_margin")
            )
            profitability["ebitda_margin"] = (
                round(ebitda / revenue * 100, 2)
                if ebitda is not None and revenue
                else provided.get("profitability", {}).get("ebitda_margin")
            )
            if "operating_margin" in profitability:
                profitability["operating_margin"] = (
                    round(operating_income / revenue * 100, 2)
                    if operating_income is not None and revenue
                    else None
                )

        cash_flow = analysis.get("cash_flow")
        if isinstance(cash_flow, dict):
            cfo = input_number("cfo", "operating_cash_flow")
            if cfo is None:
                cfo = sourced_number("cfo", "operating_cash_flow")
            capex = input_number("capex", "capital_expenditures", "capital_expenditure")
            if capex is None:
                capex = sourced_number("capex", "capital_expenditures", "capital_expenditure")
            fcf = input_number("free_cash_flow", "fcf")
            if fcf is None:
                fcf = sourced_number("free_cash_flow", "fcf")
            cfo_series = sourced_series("cfo", "operating_cash_flow")
            capex_series = sourced_series("capex", "capital_expenditure", "capital_expenditures")
            common_periods = set(cfo_series) & set(capex_series)
            if fcf is None and common_periods:
                latest_period = sorted(common_periods)[-1]
                fcf = cfo_series[latest_period] - capex_series[latest_period]
                cash_flow["free_cash_flow_basis"] = f"Derived as operating cash flow less capital expenditures for {latest_period}."
            if "operating_cash_flow" in cash_flow:
                cash_flow["operating_cash_flow"] = cfo if cfo is not None else (
                    provided.get("cash_flow", {}).get("operating_cash_flow")
                )
            if "capital_expenditures" in cash_flow:
                cash_flow["capital_expenditures"] = capex
            if "free_cash_flow" in cash_flow:
                cash_flow["free_cash_flow"] = fcf
            if "burn_rate" in cash_flow and not (
                input_number("burn_rate", "cash_burn") is not None
                or task_has_amount(r"burn rate|cash burn")
            ):
                cash_flow["burn_rate"] = None

        balance_sheet = analysis.get("balance_sheet")
        if isinstance(balance_sheet, dict):
            for field, aliases in {
                "cash": ("cash", "cash_and_cash_equivalents"),
                "long_term_debt": ("long_term_debt", "total_debt"),
                "shareholders_equity": ("shareholders_equity", "stockholders_equity", "total_equity"),
            }.items():
                value = input_number(*aliases)
                if value is None:
                    value = sourced_number(*aliases)
                if field in balance_sheet:
                    balance_sheet[field] = value
            validated_balance = provided.get("balance_sheet", {})
            for field in ("net_debt", "net_debt_to_ebitda"):
                if field in balance_sheet and validated_balance.get(field) is not None:
                    balance_sheet[field] = validated_balance[field]

        valuation = analysis.get("valuation")
        if isinstance(valuation, dict):
            validated_valuation = provided.get("valuation", {})
            for field in ("enterprise_value", "equity_value", "ev_ebitda_multiple"):
                if validated_valuation.get(field) is not None:
                    valuation[field] = validated_valuation[field]

            if validated_valuation.get("ev_ebitda_multiple") is not None:
                # A supplied EV/EBITDA calculation is not a market multiple estimate or a DCF.
                valuation["multiple_estimate"] = None
                if not any(
                    item.get("success") and item.get("name", "").lower() in valuation_tools
                    and contains_numeric_field(item.get("data"), ("dcf_estimate", "dcf_value"))
                    for item in tool_results if isinstance(item, dict)
                ):
                    valuation["dcf_estimate"] = None

            valuation_fields = ("dcf_estimate", "multiple_estimate", "enterprise_value", "equity_value", "valuation")
            explicit_valuation = contains_numeric_field(context, valuation_fields) or task_has_amount(
                r"valuation|enterprise value|equity value|dcf"
            )
            has_valuation_method = any(
                item.get("success") and item.get("name", "").lower() in valuation_tools
                and contains_numeric_field(item.get("data"), valuation_fields)
                for item in tool_results if isinstance(item, dict)
            )
            if not explicit_valuation and not has_valuation_method:
                for field in ("dcf_estimate", "multiple_estimate"):
                    valuation[field] = None
                if isinstance(valuation.get("confidence_range"), dict):
                    valuation["confidence_range"].update({"low": None, "high": None})

        if metric_conflicts:
            analysis["metric_conflicts"] = metric_conflicts
            analysis["human_review_required"] = True
            analysis["confidence_limitations"] = (
                "The model output conflicted with deterministic calculations from supplied inputs; "
                "structured values were corrected, but related narrative requires review."
            )

        equity_known = input_number(
            "equity", "total_equity", "shareholders_equity", "stockholders_equity"
        ) is not None or sourced_number(
            "equity", "total_equity", "shareholders_equity", "stockholders_equity"
        ) is not None
        risks = analysis.get("financial_risks")
        if isinstance(risks, list) and not equity_known:
            filtered = [
                risk for risk in risks
                if not isinstance(risk, str)
                or not re.search(r"debt[- ]to[- ]equity|debt/equity|equity ratio", risk, re.I)
            ]
            if len(filtered) != len(risks):
                filtered.append("Equity was not provided, so debt-to-equity cannot be assessed.")
                analysis["financial_risks"] = filtered

        # A fluent narrative or fabricated fields must not inflate evidentiary confidence.
        if (isinstance(revenue_analysis, dict) and revenue_analysis.get("annual_revenue") is None) or (
            isinstance(cash_flow, dict) and any(cash_flow.get(k) is None for k in (
            "operating_cash_flow", "free_cash_flow", "burn_rate"
            ))) or (isinstance(valuation, dict) and valuation.get("dcf_estimate") is None):
            analysis["confidence_limitations"] = "Confidence is capped because key financial inputs or source evidence are missing."
            if str(analysis.get("recommendation", "")).lower() in {
                "proceed", "approve", "advance", "go", "invest"
            }:
                analysis["recommendation"] = "caution"
                analysis["recommendation_limitations"] = (
                    "An affirmative deal signal requires adequate profitability, cash-flow, and valuation evidence."
                )

    def _provided_metrics_assessment(self, task: str) -> Dict[str, Any]:
        """Build a limited assessment when the model returns no usable content."""
        text = task.lower()

        def percent_before(label: str) -> Optional[float]:
            match = re.search(
                rf"(-?\d+(?:\.\d+)?)\s*%\s*(?:annual\s+)?(?:{label})", text
            )
            if match:
                return float(match.group(1))
            match = re.search(
                rf"(?:{label})\s*(?:of\s*)?(-?\d+(?:\.\d+)?)\s*%", text
            )
            return float(match.group(1)) if match else None

        scale = {
            "thousand": 1_000, "thousands": 1_000, "k": 1_000,
            "million": 1_000_000, "millions": 1_000_000, "mn": 1_000_000, "m": 1_000_000,
            "billion": 1_000_000_000, "billions": 1_000_000_000, "bn": 1_000_000_000, "b": 1_000_000_000,
        }
        shared_unit = re.search(
            r"\b(?:usd\s*)?(?:in\s*)?(billions?|millions?|thousands?|bn|mn)\b|\busd\s*([kmb])\b",
            text,
        )
        shared_multiplier = scale.get(
            next((group for group in (shared_unit.groups() if shared_unit else ()) if group), ""),
            1,
        )
        amount = (
            r"(?:\$|usd\s*)?\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
            r"\s*(billion|million|thousand|bn|mn|[kmb])?\b"
        )

        def amount_value(value: str, unit: Optional[str]) -> float:
            unit_key = (unit or "").lower()
            return float(value.replace(",", "")) * scale.get(unit_key, shared_multiplier)

        def amount_for(label: str) -> Optional[float]:
            match = re.search(rf"{amount}\s*(?:{label})\b", text)
            if not match:
                match = re.search(
                    rf"(?:{label})\s*(?:(?:was|were|is|of|at|equals?)\s*)?{amount}",
                    text,
                )
            if not match:
                return None
            if re.search(r"\bfy\s*$", text[:match.start()], re.IGNORECASE):
                return None
            return amount_value(match.group(1), match.group(2))

        def annual_series(label: str) -> Dict[str, List[float]]:
            series: Dict[str, List[float]] = {}
            pattern = re.compile(
                rf"\b(?:fy\s*(20\d{{2}}|\d{{2}})|(20\d{{2}}))\b"
                rf"(?:(?!\b(?:fy\s*(?:20\d{{2}}|\d{{2}})|(20\d{{2}}))\b)[^;\n]){{0,120}}?"
                rf"\b(?:annual\s+)?{label}"
                rf"\s*(?:(?:was|were|is|of|at|equals?)\s*)?{amount}",
                re.IGNORECASE,
            )
            for match in pattern.finditer(text):
                raw_year = match.group(1) or match.group(2)
                year = f"20{raw_year}" if len(raw_year) == 2 else raw_year
                series.setdefault(year, []).append(amount_value(match.group(4), match.group(5)))
            return series

        revenue_series = annual_series(r"revenue")
        ebitda_series = annual_series(r"ebitda")
        gross_profit_series = annual_series(r"gross\s+profit")
        inferred_periods = set()
        fiscal_periods = list(re.finditer(r"\bfy\s*(20\d{2}|\d{2})\b", text))
        for index, period_match in enumerate(fiscal_periods):
            raw_year = period_match.group(1)
            year = f"20{raw_year}" if len(raw_year) == 2 else raw_year
            end = fiscal_periods[index + 1].start() if index + 1 < len(fiscal_periods) else len(text)
            period_text = text[period_match.end():end]
            shorthand = re.match(
                rf"\s*[,=:]?\s*{amount}(?:\s*(?:,\s*and|and|,)\s*{amount})?",
                period_text,
                re.IGNORECASE,
            )
            if not shorthand:
                continue

            first_value = amount_value(shorthand.group(1), shorthand.group(2))
            assigned = False
            if year not in revenue_series and revenue_series:
                revenue_series[year] = [first_value]
                assigned = True

            second_value = (
                amount_value(shorthand.group(3), shorthand.group(4))
                if shorthand.group(3) else None
            )
            if (
                second_value is not None
                and year not in ebitda_series
                and ebitda_series
                and "and" in shorthand.group(0)
            ):
                ebitda_series[year] = [second_value]
                assigned = True
            if assigned:
                inferred_periods.add(year)

        annual_financials = []
        ambiguous_inputs = []
        all_series = {
            "revenue": revenue_series,
            "ebitda": ebitda_series,
            "gross_profit": gross_profit_series,
        }
        for year in sorted(set().union(*(set(values) for values in all_series.values()))):
            row = {
                "period": f"FY{year}",
                "source_type": (
                    "user_supplied_shorthand_inferred"
                    if year in inferred_periods else "user_supplied_unverified"
                ),
            }
            for metric, metric_series in all_series.items():
                values = metric_series.get(year, [])
                if len(values) == 1:
                    row[metric] = values[0]
                elif len(values) > 1:
                    row[metric] = None
                    ambiguous_inputs.append({
                        "period": f"FY{year}", "metric": metric,
                        "competing_values": values,
                        "limitation": "Multiple supplied values for the same period; no source was selected.",
                    })
            annual_financials.append(row)

        revenue_growth = None
        revenue_cagr = None
        ebitda_growth = None
        valid_revenue = [row for row in annual_financials if isinstance(row.get("revenue"), (int, float))]
        valid_ebitda = [row for row in annual_financials if isinstance(row.get("ebitda"), (int, float))]
        if len(valid_revenue) >= 2:
            prior, latest = valid_revenue[-2:]
            if int(latest["period"][2:]) == int(prior["period"][2:]) + 1 and prior["revenue"] > 0:
                revenue_growth = round((latest["revenue"] / prior["revenue"] - 1) * 100, 2)
            first, last = valid_revenue[0], valid_revenue[-1]
            year_span = int(last["period"][2:]) - int(first["period"][2:])
            if year_span > 0 and first["revenue"] > 0 and last["revenue"] >= 0:
                revenue_cagr = round(((last["revenue"] / first["revenue"]) ** (1 / year_span) - 1) * 100, 2)
        if len(valid_ebitda) >= 2:
            prior, latest = valid_ebitda[-2:]
            if int(latest["period"][2:]) == int(prior["period"][2:]) + 1 and prior["ebitda"] > 0:
                ebitda_growth = round((latest["ebitda"] / prior["ebitda"] - 1) * 100, 2)

        arr = amount_for(r"(?:arr|annual recurring revenue)")
        revenue = amount_for(r"(?:annual\s+)?revenue")
        gross_profit = amount_for(r"gross\s+profit")
        ebitda = amount_for(r"ebitda")
        cash = amount_for(r"cash(?:\s+balance)?")
        debt = amount_for(r"(?:total\s+)?debt")
        enterprise_value = amount_for(r"enterprise\s+value|\bev\b")
        annualized_burn = amount_for(r"(?:annualized|annual)\s+(?:net\s+)?burn|net\s+burn")

        growth = percent_before(r"(?:arr\s+|revenue\s+)?growth|(?:revenue\s+)?cagr")
        if annual_financials:
            newest = annual_financials[-1]
            if "revenue" in newest:
                revenue = newest["revenue"]
            if "ebitda" in newest:
                ebitda = newest["ebitda"]
            if "gross_profit" in newest:
                gross_profit = newest["gross_profit"]
        if revenue_cagr is not None:
            growth = revenue_cagr
        elif growth is None:
            growth = revenue_growth
        # A gross margin must never be relabelled as EBITDA margin.
        ebitda_margin = percent_before(r"ebitda\s+margin")
        gross_margin = percent_before(r"gross\s+margin")
        gross_margin_is_derived = False
        if revenue and gross_profit is not None:
            gross_margin = round(gross_profit / revenue * 100, 2)
            gross_margin_is_derived = True
        if revenue and ebitda is not None:
            ebitda_margin = round(ebitda / revenue * 100, 2)
        churn = percent_before(r"churn")
        gross_retention = percent_before(r"gross revenue retention|gross retention|grr")
        net_retention = percent_before(r"net revenue retention|net retention|nrr")
        payback_match = re.search(
            r"(?:cac\s+)?payback(?:\s+period)?\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*(months?|mos?)?",
            text,
        )
        cac_payback = float(payback_match.group(1)) if payback_match else None
        ltv_match = re.search(
            r"(?:ltv\s*/\s*cac|ltv-to-cac|lifetime value to cac)\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*x?",
            text,
        )
        ltv_cac = float(ltv_match.group(1)) if ltv_match else None
        if all(value is None for value in (
            arr, revenue, gross_profit, ebitda, cash, debt, growth, ebitda_margin,
            gross_margin, churn, gross_retention, net_retention, cac_payback,
            ltv_cac, annualized_burn,
        )):
            return {}

        risks = []
        if ebitda_margin is not None and ebitda_margin < 0:
            risks.append(
                f"Negative EBITDA margin ({ebitda_margin:g}%) indicates operating losses; cash runway cannot be estimated without cash flow and balance-sheet data."
            )
        if churn is not None and churn >= 10:
            risks.append(
                f"Reported churn of {churn:g}% may materially erode recurring revenue; its measurement period and gross/net basis were not specified."
            )
        if growth is not None and growth >= 40 and ebitda_margin is not None and ebitda_margin < 0:
            risks.append(
                "High reported growth alongside negative EBITDA suggests growth is not yet converting into profitability; retention and acquisition costs need review."
            )

        net_debt = debt - cash if debt is not None and cash is not None else None
        equity_value = enterprise_value - net_debt if enterprise_value is not None and net_debt is not None else None
        ev_ebitda_multiple = (
            round(enterprise_value / ebitda, 2)
            if enterprise_value is not None and ebitda not in (None, 0)
            else None
        )
        net_debt_to_ebitda = (
            round(net_debt / ebitda, 2)
            if net_debt is not None and ebitda and ebitda > 0
            else None
        )
        cash_runway_months = (
            round(cash / annualized_burn * 12, 1)
            if cash is not None and annualized_burn is not None and annualized_burn > 0
            else None
        )

        facts = []
        if revenue is not None:
            facts.append("Revenue: " + format(revenue, ",.0f") + " USD (user supplied)")
        if gross_profit is not None:
            facts.append("Gross profit: " + format(gross_profit, ",.0f") + " USD (user supplied)")
        if cash is not None:
            facts.append("Cash: " + format(cash, ",.0f") + " USD (user supplied)")
        if gross_retention is not None:
            facts.append(f"Gross revenue retention: {gross_retention:g}% (user supplied)")
        if net_retention is not None:
            facts.append(f"Net revenue retention: {net_retention:g}% (user supplied)")
        if cac_payback is not None:
            facts.append(f"CAC payback: {cac_payback:g} months (user supplied)")
        if ltv_cac is not None:
            facts.append(f"LTV/CAC: {ltv_cac:g}x (user supplied)")
        if annualized_burn is not None:
            facts.append(f"Annualized net burn: ${annualized_burn:,.0f} (user supplied)")
        if debt is not None:
            facts.append("Debt: " + format(debt, ",.0f") + " USD (user supplied)")
        if net_debt is not None:
            facts.append("Net debt: " + format(net_debt, ",.0f") + " USD (derived as debt less cash)")
        if arr is not None:
            facts.append(f"ARR: ${arr:,.0f} (user supplied)")
        if ebitda is not None:
            facts.append(f"EBITDA: ${ebitda:,.0f} (user supplied; period alignment not verified)")
        if growth is not None:
            growth_basis = "derived CAGR" if revenue_cagr is not None else "user supplied"
            facts.append(f"Revenue growth: {growth:g}% ({growth_basis})")
        if ebitda_margin is not None and not (revenue is not None and ebitda is not None):
            facts.append(f"EBITDA margin: {ebitda_margin:g}% (user supplied)")
        if churn is not None:
            facts.append(f"churn: {churn:g}% (user supplied)")
        if gross_margin is not None:
            source = "derived from gross profit and revenue" if gross_margin_is_derived else "user supplied"
            facts.append(f"Gross margin: {gross_margin:g}% ({source})")
        if ebitda_margin is not None and revenue is not None and ebitda is not None:
            facts.append(f"EBITDA margin: {ebitda_margin:g}% (derived as EBITDA divided by revenue)")
        if net_debt_to_ebitda is not None:
            facts.append(f"net debt/EBITDA: {net_debt_to_ebitda:g}x (derived)")
        if enterprise_value is not None:
            facts.append(f"Enterprise value: {enterprise_value:,.0f} USD (user supplied)")
        if equity_value is not None:
            facts.append(f"Implied equity value: {equity_value:,.0f} USD (derived as enterprise value less net debt)")
        if ev_ebitda_multiple is not None:
            facts.append(f"EV/EBITDA: {ev_ebitda_multiple:.2f}x (derived)")

        key_findings = []
        if revenue_cagr is not None:
            first, last = valid_revenue[0], valid_revenue[-1]
            start_year = first["period"][2:]
            end_year = last["period"][2:]
            key_findings.append({
                "finding": "revenue_cagr",
                "text": (
                    f"Revenue CAGR from FY{start_year} to FY{end_year} was {revenue_cagr:.2f}% "
                    f"= (({last['revenue']:,.0f} / {first['revenue']:,.0f})^(1/{int(end_year) - int(start_year)}) - 1) × 100."
                ),
                "period_start": start_year,
                "period_end": end_year,
                "value_percent": revenue_cagr,
                "source_type": "derived_from_user_supplied_revenue",
            })
        derived_metrics = []
        if gross_margin is not None:
            derived_metrics.append({"name": "gross_margin", "value": gross_margin, "unit": "percent", "formula": "gross profit / revenue × 100", "source_type": "derived_from_user_inputs"})
        if ebitda_margin is not None:
            derived_metrics.append({"name": "ebitda_margin", "value": ebitda_margin, "unit": "percent", "formula": "EBITDA / revenue × 100", "source_type": "derived_from_user_inputs"})
        if net_debt is not None:
            derived_metrics.append({"name": "net_debt", "value": net_debt, "unit": "USD", "formula": "debt − cash", "source_type": "derived_from_user_inputs"})
        if ev_ebitda_multiple is not None:
            derived_metrics.append({"name": "ev_ebitda_multiple", "value": ev_ebitda_multiple, "unit": "multiple", "formula": "enterprise value / EBITDA", "source_type": "derived_from_user_inputs"})
        if equity_value is not None:
            derived_metrics.append({"name": "equity_value", "value": equity_value, "unit": "USD", "formula": "enterprise value − net debt", "source_type": "derived_from_user_inputs"})

        deterministic_reasoning = "\n".join([
            "Supplied, unverified case data; no external financial source was used.",
            *(f"- {item['text']}" for item in key_findings),
            *(f"- Gross margin: {gross_margin:g}% (derived from gross profit / revenue)." if gross_margin_is_derived else f"- Gross margin: {gross_margin:g}% (user supplied)." for _ in [0] if gross_margin is not None),
            *(f"- FY25 EBITDA margin: {ebitda_margin:.2f}% = EBITDA / revenue." for _ in [0] if ebitda_margin is not None),
            *(f"- Net debt: {net_debt:,.0f} USD = debt − cash." for _ in [0] if net_debt is not None),
            *(f"- EV/EBITDA: {ev_ebitda_multiple:.2f}x = enterprise value / EBITDA." for _ in [0] if ev_ebitda_multiple is not None),
            *(f"- Implied equity value: {equity_value:,.0f} USD = enterprise value − net debt." for _ in [0] if equity_value is not None),
            *(f"- Cash runway: {cash_runway_months:g} months (cash / annualized net burn × 12; assumes constant burn)." for _ in [0] if cash_runway_months is not None),
            "Unknown: operating cash flow, free cash flow, forecasts, and valuation adjustments were not supplied.",
            *(["Revenue growth trend was not established from the supplied inputs."] if growth is None else []),
        ])
        if cash_runway_months is not None:
            facts.append(f"Cash runway: {cash_runway_months:g} months (derived; constant burn assumed)")
        data_limitations = [
            "User-provided inputs only",
            "Inputs were not independently verified",
            "Margins are based on a single supplied-period snapshot",
            "Operating cash flow and free cash flow were not supplied",
            "No DCF or forecast-based valuation was estimated; forecasts, discount-rate assumptions, and comparable-company evidence are missing",
        ]
        if inferred_periods:
            data_limitations.append(
                "Some revenue or EBITDA values were mapped from unlabeled fiscal-year shorthand using the surrounding labeled rows; confirm the period-to-metric mapping against source records."
            )

        return {
            "historical_financials": annual_financials,
            "input_conflicts": ambiguous_inputs,
            "key_findings": key_findings,
            "derived_metrics": derived_metrics,
            "derived_growth": {
                "revenue_yoy_percent": revenue_growth,
                "ebitda_yoy_percent": ebitda_growth,
                "revenue_cagr_percent": revenue_cagr,
                "method": "CAGR = (ending revenue / beginning revenue)^(1 / year span) - 1; YoY = current / prior - 1; derived from supplied inputs",
            },
            "revenue_analysis": {
                "annual_revenue": revenue,
                "arr": arr,
                "growth_rate": growth,
                "growth_basis": (
                    "CAGR across supplied fiscal-year endpoints"
                    if revenue_cagr is not None
                    else "latest consecutive-year growth"
                    if revenue_growth is not None
                    else "user supplied"
                    if growth is not None
                    else None
                ),
                "quality_assessment": "Based only on user-provided metrics; no filings or external financial data were verified.",
            },
            "profitability": {
                "gross_profit": gross_profit,
                "gross_margin": gross_margin,
                "ebitda": ebitda,
                "ebitda_margin": ebitda_margin,
                "trend": "Loss-making on an EBITDA basis" if ebitda_margin is not None and ebitda_margin < 0 else "Single-period snapshot; trend not established",
            },
            "cash_flow": {
                "operating_cash_flow": None,
                "free_cash_flow": None,
                "burn_rate": None,
                "annualized_net_burn": annualized_burn,
                "cash_runway_months": cash_runway_months,
                "cash_runway_assumption": (
                    "Cash divided by supplied annualized net burn, assuming the burn rate continues evenly."
                    if cash_runway_months is not None else None
                ),
            },
            "balance_sheet": {
                "cash": cash,
                "debt": debt,
                "net_debt": net_debt,
                "net_debt_to_ebitda": net_debt_to_ebitda,
            },
            "valuation": {
                "dcf_estimate": None,
                "multiple_estimate": None,
                "enterprise_value": enterprise_value,
                "equity_value": equity_value,
                "ev_ebitda_multiple": ev_ebitda_multiple,
                "confidence_range": {"low": None, "high": None},
            },
            "customer_metrics": {
                "churn_rate": churn,
                "gross_revenue_retention": gross_retention,
                "net_revenue_retention": net_retention,
                "cac_payback_months": cac_payback,
                "ltv_to_cac": ltv_cac,
            },
            "financial_risks": (
                risks + ["Retention and CAC metrics are user supplied; cohort definitions, measurement periods, and calculation methods were not verified."]
                if any(value is not None for value in (gross_retention, net_retention, cac_payback, ltv_cac))
                else risks or ["Financial risk cannot be fully assessed from a single-period snapshot; cash conversion, debt terms, and historical trends are missing."]
            ),
            "investment_thesis": (
                "Preliminary calculations use supplied, unverified figures only. They are not an investment recommendation; "
                "no market valuation, forecasts, cash-flow analysis, or source verification was performed. "
                + ("Revenue growth trend was not established. " if growth is None else "")
                + (f"Gross revenue retention: {gross_retention:g}%. " if gross_retention is not None else "")
                + (f"Net revenue retention: {net_retention:g}%. " if net_retention is not None else "")
            ),
            "reasoning": deterministic_reasoning,
            "recommendation": "caution",
            "data_limitations": data_limitations,
        }

    @staticmethod
    def _statement_data_assessment(
        financial_data: Dict[str, Any], period_limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """Create a deterministic readout from fetched, source-linked statements."""
        income = financial_data.get("income_statement", {})
        balance = financial_data.get("balance_sheet", {})
        cash_flow = financial_data.get("cash_flow", {})

        def series(statement: Dict[str, Any], metric: str) -> Dict[str, float]:
            aliases = {
                "revenue": ("revenue", "total_revenue"),
                "gross_profit": ("gross_profit",),
                "operating_income": ("operating_income",),
                "net_income": ("net_income", "net_income_common_stockholders"),
                "cfo": ("cfo", "operating_cash_flow"),
                "capex": ("capex", "capital_expenditure", "capital_expenditures"),
                "cash": ("cash", "cash_and_cash_equivalents", "cash_cash_equivalents_and_short_term_investments"),
                "long_term_debt": ("long_term_debt", "long_term_debt_and_capital_lease_obligation", "total_debt"),
            }
            if not isinstance(statement, dict):
                return {}
            for alias in aliases.get(metric, (metric,)):
                values = statement.get(alias, {})
                if isinstance(values, dict):
                    normalized = {
                        str(period): float(value)
                        for period, value in values.items()
                        if period != "_sources" and isinstance(value, (int, float))
                    }
                    if normalized:
                        return normalized
            return {}

        revenue = series(income, "revenue")
        gross_profit = series(income, "gross_profit")
        operating_income = series(income, "operating_income")
        net_income = series(income, "net_income")
        cfo = series(cash_flow, "cfo")
        capex = series(cash_flow, "capex")
        cash = series(balance, "cash")
        debt = series(balance, "long_term_debt")
        years = sorted(set(revenue) | set(gross_profit) | set(operating_income) | set(net_income))
        if period_limit and period_limit > 0:
            years = years[-period_limit:]

        periods = []
        revenue_sources = income.get("_sources", {}).get("revenue", {})
        for index, period in enumerate(years):
            row: Dict[str, Any] = {"period": period, "fiscal_year": f"FY{period}"}
            filing = revenue_sources.get(period, {}) if isinstance(revenue_sources, dict) else {}
            if isinstance(filing, dict):
                if filing.get("end"):
                    row["period_end_date"] = filing["end"]
                if filing.get("form"):
                    row["filing_form"] = filing["form"]
                if filing.get("filed"):
                    row["filing_date"] = filing["filed"]
                if filing.get("url"):
                    row["source_url"] = filing["url"]
            for name, values in (("revenue", revenue), ("gross_profit", gross_profit),
                                 ("operating_income", operating_income), ("net_income", net_income)):
                if period in values:
                    row[name] = values[period]
            if period in revenue and revenue[period]:
                if period in gross_profit:
                    row["gross_margin_percent"] = round(gross_profit[period] / revenue[period] * 100, 2)
                if period in operating_income:
                    row["operating_margin_percent"] = round(operating_income[period] / revenue[period] * 100, 2)
            if index:
                previous = years[index - 1]
                if period.isdigit() and previous.isdigit() and int(period) == int(previous) + 1:
                    if period in revenue and previous in revenue and revenue[previous] > 0:
                        row["revenue_yoy_percent"] = round((revenue[period] / revenue[previous] - 1) * 100, 2)
            periods.append(row)

        revenue_periods = sorted(revenue)
        if period_limit and period_limit > 0:
            revenue_periods = revenue_periods[-period_limit:]
        growth = None
        if len(revenue_periods) > 1:
            prior, current = revenue_periods[-2:]
            if prior.isdigit() and current.isdigit() and int(current) == int(prior) + 1 and revenue[prior] > 0:
                growth = round((revenue[current] / revenue[prior] - 1) * 100, 2)

        key_findings = []
        if len(revenue_periods) >= 2:
            first_period, last_period = revenue_periods[0], revenue_periods[-1]
            if first_period.isdigit() and last_period.isdigit() and int(last_period) > int(first_period) and revenue[first_period] > 0:
                span = int(last_period) - int(first_period)
                cagr = round(((revenue[last_period] / revenue[first_period]) ** (1 / span) - 1) * 100, 2)
                key_findings.append({
                    "finding": "revenue_cagr",
                    "text": f"Revenue increased from {first_period} to {last_period} at a calculated {cagr:.2f}% CAGR.",
                    "period_start": first_period,
                    "period_end": last_period,
                    "value_percent": cagr,
                    "source_type": "derived_from_reported_revenue",
                })
        annual_growth = [
            (period, row.get("revenue_yoy_percent"))
            for period, row in zip(years, periods)
            if row.get("revenue_yoy_percent") is not None
        ]
        if len(annual_growth) >= 2:
            (prior_year, prior_growth), (latest_year, latest_growth) = annual_growth[-2:]
            change = round(latest_growth - prior_growth, 2)
            direction = "accelerated" if change > 0 else "decelerated" if change < 0 else "was unchanged"
            key_findings.append({
                "finding": "revenue_growth_trend",
                "text": f"Revenue growth {direction} by {abs(change):.2f} percentage points, from {prior_growth:.2f}% in FY{prior_year} to {latest_growth:.2f}% in FY{latest_year}.",
                "period_start": prior_year,
                "period_end": latest_year,
                "change_percentage_points": change,
                "source_type": "derived_from_reported_revenue",
            })
        operating_margin_periods = [
            row for row in periods if row.get("operating_margin_percent") is not None
        ]
        if len(operating_margin_periods) >= 2:
            first_margin, last_margin = operating_margin_periods[0], operating_margin_periods[-1]
            change = round(last_margin["operating_margin_percent"] - first_margin["operating_margin_percent"], 2)
            key_findings.append({
                "finding": "operating_margin_change",
                "text": f"Operating margin changed by {change:+.2f} percentage points, from {first_margin['operating_margin_percent']:.2f}% in {first_margin['fiscal_year']} to {last_margin['operating_margin_percent']:.2f}% in {last_margin['fiscal_year']}.",
                "period_start": first_margin["period"],
                "period_end": last_margin["period"],
                "change_percentage_points": change,
                "source_type": "derived_from_reported_operating_income_and_revenue",
            })

        allowed_source_periods = set(revenue_periods)
        sources = []
        for statement in (income, balance, cash_flow):
            maps = statement.get("_sources", {}) if isinstance(statement, dict) else {}
            for by_period in maps.values():
                for period, metadata in by_period.items():
                    if (
                        isinstance(metadata, dict)
                        and metadata.get("url")
                        and str(period) in allowed_source_periods
                        and str(metadata.get("reported_fy", period)) == str(period)
                    ):
                        sources.append({
                            "period": period,
                            "title": f"{financial_data.get('entity_name', 'Company')} {metadata.get('form', 'SEC filing')}",
                            "url": metadata["url"], "form": metadata.get("form"),
                            "filed": metadata.get("filed"), "accession": metadata.get("accession"),
                        })
        sources = list({(item["url"], item["period"]): item for item in sources}.values())

        def latest(values: Dict[str, float]) -> tuple[Optional[str], Optional[float]]:
            period = sorted(values)[-1] if values else None
            return period, values[period] if period else None

        entity = financial_data.get("entity_name", financial_data.get("ticker", "Company"))
        gross_profit_period, gross_profit_latest = latest(gross_profit)
        revenue_period, revenue_latest = latest(revenue)
        op_income_period, op_income_latest = latest(operating_income)
        cfo_period, cfo_latest = latest(cfo)
        # Do not present an older capex fact as if it belonged to the latest CFO period.
        # FCF below remains available for the most recent period with both inputs.
        capex_period = cfo_period if cfo_period in capex else None
        capex_latest = capex.get(capex_period) if capex_period else None
        aligned_cash_flow_periods = set(cfo) & set(capex)
        fcf_period = sorted(aligned_cash_flow_periods)[-1] if aligned_cash_flow_periods else None
        free_cash_flow = (
            round(cfo[fcf_period] - capex[fcf_period], 2)
            if fcf_period else None
        )
        cash_period, cash_latest = latest(cash)
        debt_period, debt_latest = latest(debt)
        source_maps = {
            "income_statement": income.get("_sources", {}),
            "balance_sheet": balance.get("_sources", {}),
            "cash_flow": cash_flow.get("_sources", {}),
        }

        def source_for(statement: str, metric: str, period: Optional[str]) -> Dict[str, Any]:
            if not period:
                return {}
            mapping = source_maps.get(statement, {})
            aliases = {
                "cfo": ("cfo", "operating_cash_flow"),
                "capex": ("capex", "capital_expenditure", "capital_expenditures"),
                "cash": ("cash", "cash_and_cash_equivalents", "cash_cash_equivalents_and_short_term_investments"),
                "long_term_debt": ("long_term_debt", "long_term_debt_and_capital_lease_obligation", "total_debt"),
            }
            for alias in aliases.get(metric, (metric,)):
                metadata = mapping.get(alias, {}).get(period, {})
                if isinstance(metadata, dict):
                    return metadata
            return {}

        cfo_source = source_for("cash_flow", "cfo", cfo_period)
        capex_source = source_for("cash_flow", "capex", capex_period)
        fcf_cfo_source = source_for("cash_flow", "cfo", fcf_period)
        fcf_capex_source = source_for("cash_flow", "capex", fcf_period)
        cash_source = source_for("balance_sheet", "cash", cash_period)
        debt_source = source_for("balance_sheet", "long_term_debt", debt_period)
        source_name = financial_data.get("source", "unknown_source")
        is_sec = source_name == "sec_edgar_companyfacts"
        if not periods:
            return {
                "company": entity, "ticker": financial_data.get("ticker"),
                "data_source": source_name, "source_url": financial_data.get("source_url"),
                "retrieved_at": financial_data.get("retrieved_at"),
                "historical_financials": [],
                "data_limitations": ["The provider returned data, but no supported standardized financial metrics could be extracted."],
                "reasoning": "No usable standardized financial facts were extracted; no analysis was generated.",
            }
        return {
            "company": entity,
            "ticker": financial_data.get("ticker"),
            "data_source": source_name,
            "source_url": financial_data.get("source_url"),
            "retrieved_at": financial_data.get("retrieved_at"),
            "confidence_basis": "not_calibrated_source_report",
            "historical_financials": periods,
            "key_findings": key_findings,
            "revenue_analysis": {
                "annual_revenue": revenue_latest,
                "growth_rate": growth,
                "growth_basis": f"Derived from consecutive annual revenue values retrieved from {source_name}." if growth is not None else None,
            },
            "profitability": {
                "gross_profit": gross_profit_latest,
                "gross_margin": round(gross_profit_latest / revenue_latest * 100, 2)
                if gross_profit_latest is not None and revenue_latest else None,
                "operating_income": op_income_latest,
                "operating_margin": round(op_income_latest / revenue_latest * 100, 2)
                if op_income_latest is not None and revenue_latest else None,
                "net_income": latest(net_income)[1], "ebitda": None, "ebitda_margin": None,
            },
            "cash_flow": {
                "operating_cash_flow": cfo_latest,
                "capital_expenditures": capex_latest,
                "free_cash_flow": free_cash_flow,
                "fiscal_year": f"FY{cfo_period}" if cfo_period else None,
                "capital_expenditures_fiscal_year": f"FY{capex_period}" if capex_period else None,
                "free_cash_flow_fiscal_year": f"FY{fcf_period}" if fcf_period else None,
                "operating_cash_flow_source_url": cfo_source.get("url"),
                "capital_expenditures_source_url": capex_source.get("url"),
                "free_cash_flow_source_urls": list(dict.fromkeys(
                    url for url in (fcf_cfo_source.get("url"), fcf_capex_source.get("url")) if url
                )),
            },
            "balance_sheet": {
                "cash": cash_latest, "cash_fiscal_year": f"FY{cash_period}" if cash_period else None,
                "cash_source_url": cash_source.get("url"),
                "long_term_debt": debt_latest, "debt_fiscal_year": f"FY{debt_period}" if debt_period else None,
                "debt_source_url": debt_source.get("url"),
            },
            "valuation": {"dcf_estimate": None, "multiple_estimate": None},
            "sources": sources,
            "data_limitations": [
                ("SEC company-facts values are extracted from XBRL; inspect the linked filing for definitions, restatements, and context."
                 if is_sec else "Financial values are secondary-vendor data, not independently verified against a primary filing."),
                "EBITDA is not established by the extracted GAAP statement fields and remains unknown.",
                "This deterministic fallback does not generate a valuation or transaction recommendation.",
            ],
            "reasoning": (
                f"Reported financial data for {entity} was retrieved from {source_name}. "
                "Margins and revenue growth are calculated only where same-period inputs are present. EBITDA, valuation, "
                "and unsupported values remain unknown."
            ),
        }

    def _calculate_confidence(self, analysis_data: Dict) -> float:
        """Calculate confidence score for analysis"""
        confidence = 0.5

        # More data = higher confidence
        if analysis_data.get("revenue_analysis", {}).get("annual_revenue"):
            confidence += 0.15

        if analysis_data.get("valuation", {}).get("dcf_estimate"):
            confidence += 0.15

        if analysis_data.get("cash_flow", {}).get("operating_cash_flow"):
            confidence += 0.1

        # Reasoning present
        if analysis_data.get("reasoning") and len(analysis_data["reasoning"]) > 100:
            confidence += 0.1

        if analysis_data.get("confidence_limitations"):
            return min(0.5, confidence)
        return min(0.85, confidence)

    async def run_valuation(
        self, financial_data: Dict[str, Any], method: str = "dcf"
    ) -> Dict[str, Any]:
        """
        Run specific valuation method

        Args:
            financial_data: Financial data for valuation
            method: Valuation method (dcf, multiples, combo)

        Returns:
            Valuation results
        """
        prompt = f"""Calculate {method.upper()} valuation for:

{json.dumps(financial_data, indent=2)}

Provide detailed calculation steps and final valuation."""

        # valuations should be reproducible; force low temperature
        response = await self.llm.generate(
            prompt,
            self._build_system_prompt(),
            temperature=0.0,
        )

        return {
            "method": method,
            "valuation": response["content"],
            "raw_data": financial_data,
        }

    # ===== Deterministic Financial Calculations =====

    @staticmethod
    def calculate_dcf(
        projected_fcf: List[float],
        wacc: float,
        terminal_growth: float = 0.025,
        mid_year_convention: bool = True,
    ) -> Dict[str, Any]:
        """
        Deterministic DCF calculation — 0% arithmetic error tolerance.
        Uses mid-year convention (stub period handling) by default per PRD.
        """
        if wacc <= terminal_growth:
            raise ValueError(
                f"WACC ({wacc}) must be > terminal growth ({terminal_growth})"
            )

        pv_cash_flows = []
        for i, fcf in enumerate(projected_fcf):
            period = i + 0.5 if mid_year_convention else i + 1
            pv = fcf / ((1 + wacc) ** period)
            pv_cash_flows.append({"year": i + 1, "fcf": fcf, "pv": round(pv, 2)})

        # Terminal Value (Gordon Growth Model)
        last_fcf = projected_fcf[-1]
        terminal_value = (last_fcf * (1 + terminal_growth)) / (wacc - terminal_growth)
        terminal_period = len(projected_fcf) + (0.5 if mid_year_convention else 1)
        pv_terminal = terminal_value / ((1 + wacc) ** terminal_period)

        enterprise_value = sum(pv["pv"] for pv in pv_cash_flows) + pv_terminal

        return {
            "method": "dcf",
            "mid_year_convention": mid_year_convention,
            "wacc": wacc,
            "terminal_growth": terminal_growth,
            "pv_cash_flows": pv_cash_flows,
            "terminal_value": round(terminal_value, 2),
            "pv_terminal_value": round(pv_terminal, 2),
            "enterprise_value": round(enterprise_value, 2),
        }

    @staticmethod
    def calculate_lbo_returns(
        entry_ev: float,
        exit_ev: float,
        equity_contribution_pct: float,
        holding_period_years: int = 5,
        total_debt_paydown: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Simplified LBO returns calculation.
        Returns IRR and MOIC for the equity sponsor.
        """
        initial_equity = entry_ev * equity_contribution_pct
        initial_debt = entry_ev - initial_equity
        remaining_debt = initial_debt - total_debt_paydown
        exit_equity = exit_ev - remaining_debt

        moic = exit_equity / initial_equity if initial_equity > 0 else 0
        irr = (moic ** (1 / holding_period_years)) - 1 if moic > 0 else -1.0

        return {
            "method": "lbo",
            "entry_ev": entry_ev,
            "exit_ev": exit_ev,
            "initial_equity": round(initial_equity, 2),
            "initial_debt": round(initial_debt, 2),
            "debt_paydown": total_debt_paydown,
            "exit_equity": round(exit_equity, 2),
            "moic": round(moic, 3),
            "irr": round(irr, 4),
        }

    @staticmethod
    def validate_unit_of_account(data: Dict[str, Any]) -> tuple[bool, List[str]]:
        """
        Validate consistent currency units across financial data.
        Flags entries where magnitude suggests unit mismatch (e.g., $M vs $K).
        """
        warnings = []
        revenue = data.get("revenue", 0)
        ebitda = data.get("ebitda", 0)
        fcf = data.get("free_cash_flow", 0)

        if revenue > 0 and ebitda > 0:
            margin = ebitda / revenue
            if margin > 1.0 or margin < -1.0:
                warnings.append(
                    f"EBITDA margin ({margin:.2%}) out of range — possible unit mismatch"
                )

        if revenue > 0 and fcf > 0:
            fcf_yield = fcf / revenue
            if fcf_yield > 1.0:
                warnings.append(
                    f"FCF yield ({fcf_yield:.2%}) exceeds revenue — possible unit mismatch"
                )

        return len(warnings) == 0, warnings


class ValuationAgent(BaseAgent):
    """Specialized agent for deal valuation"""

    name = "valuation_agent"
    description = "Specializes in company valuation using multiple methodologies"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        """Execute valuation task"""
        start_time = datetime.now()

        # Priority: Use structured FactBase if available
        fact_base = context.get("fact_base", {}) if context else {}
        
        # Fallback to secondary agent outputs
        market_data = context.get("market_data", {}) if context else {}
        financial_data = fact_base.get("metrics") or context.get("financial_data", {}) if context else {}
        deal_terms = fact_base.get("terms") or context.get("deal_terms", {}) if context else {}
        
        # Merge metrics and terms for specific valuation needs
        if fact_base:
            financial_data = {**financial_data, **deal_terms}

        # Run multiple valuation methods
        methods = ["dcf", "comparable_companies", "precedent_transactions"]
        valuations = {}

        for method in methods:
            valuations[method] = await self._run_valuation_method(
                method, financial_data, market_data
            )

        # Calculate weighted average
        weights = {
            "dcf": 0.4,
            "comparable_companies": 0.35,
            "precedent_transactions": 0.25,
        }
        valid_values = [v["value"] for v in valuations.values() if v.get("value")]
        if not valid_values:
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            return AgentOutput(
                success=False,
                data={"valuations": valuations},
                reasoning="All valuation methods returned insufficient data",
                confidence=0.0,
                execution_time_ms=execution_time,
            )

        weighted_value = sum(
            valuations[m]["value"] * weights[m]
            for m in methods
            if valuations[m].get("value")
        )

        execution_time = (datetime.now() - start_time).total_seconds() * 1000

        return AgentOutput(
            success=True,
            data={
                "valuations": valuations,
                "weighted_estimate": weighted_value,
                "valuation_range": {
                    "low": min(valid_values),
                    "high": max(valid_values),
                },
            },
            reasoning=f"Weighted valuation using {', '.join(methods)}",
            confidence=0.75,
            execution_time_ms=execution_time,
        )

    async def _run_valuation_method(
        self, method: str, financial_data: Dict, market_data: Dict
    ) -> Dict[str, Any]:
        if method == "dcf":
            cash_flows = financial_data.get("projected_cash_flows")
            wacc = financial_data.get("wacc")
            if not cash_flows or not wacc:
                return {"method": method, "value": None, "note": "Insufficient data"}

            # Use financial calculator tool
            result = await self.tools.execute(
                "financial_calculator",
                {
                    "calculation_type": "dcf",
                    "inputs": {
                        "cash_flows": cash_flows,
                        "discount_rate": wacc,
                        "terminal_growth": 0.025,
                    },
                },
            )

            if result.success:
                return {"method": method, "value": result.data.get("dcf_value", 0)}

        elif method == "comparable_companies":
            revenue = financial_data.get("revenue")
            multiple = market_data.get("ev_revenue_median")
            if not revenue or not multiple:
                return {"method": method, "value": None, "note": "Insufficient data"}
            return {"method": method, "value": revenue * multiple}

        return {"method": method, "value": None, "note": "Unsupported method"}

    @staticmethod
    def calculate_comps_valuation(
        target_metric: float,
        peer_multiples: List[float],
        metric_name: str = "EV/EBITDA",
    ) -> Dict[str, Any]:
        """
        Comparable companies valuation using peer multiples.
        Returns median, mean, and range-based valuations.
        """
        if not peer_multiples:
            return {"method": "comps", "value": None, "error": "No peer data"}

        sorted_multiples = sorted(peer_multiples)
        n = len(sorted_multiples)
        median = (
            sorted_multiples[n // 2]
            if n % 2
            else (sorted_multiples[n // 2 - 1] + sorted_multiples[n // 2]) / 2
        )
        mean = sum(sorted_multiples) / n

        return {
            "method": "comparable_companies",
            "metric_name": metric_name,
            "target_metric": target_metric,
            "peer_multiples": sorted_multiples,
            "median_multiple": round(median, 2),
            "mean_multiple": round(mean, 2),
            "implied_value_median": round(target_metric * median, 2),
            "implied_value_mean": round(target_metric * mean, 2),
            "value_range": {
                "low": round(target_metric * sorted_multiples[0], 2),
                "high": round(target_metric * sorted_multiples[-1], 2),
            },
        }
