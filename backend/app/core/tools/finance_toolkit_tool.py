"""
FinanceToolkit Tool — 150+ Ratios, Fundamentals, DCF, WACC

MCP Tool: finance_analysis
- Uses JerBouma/FinanceToolkit
- Requires FMP API Key (free tier: 250 requests/day)
- Auto-fallback to Yahoo Finance
"""

import asyncio
from typing import Dict, Any, List, Optional, Union
import os
import structlog
from app.core.tools.tool_router import BaseTool, ToolResult
from app.config import get_settings


def _try_import_toolkit():
    try:
        from financetoolkit import Toolkit

        return Toolkit
    except ImportError:
        return None


class FinanceAnalysisTool(BaseTool):
    """Comprehensive financial analysis via FinanceToolkit (150+ ratios, DCF, WACC)."""

    def __init__(self):
        super().__init__(
            name="finance_analysis",
            description="Calculate comprehensive financial ratios, WACC, DuPont analysis, and DCF intrinsic valuation. Fetches standardized financial statements automatically.",
        )
        self.logger = structlog.get_logger()

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of stock ticker symbols (e.g., ['MSFT', 'AAPL'])",
                },
                "analysis_type": {
                    "type": "string",
                    "enum": ["ratios", "dcf", "wacc", "dupont", "fundamentals"],
                    "description": "The type of analysis to perform",
                    "default": "ratios",
                },
                "sub_type": {
                    "type": "string",
                    "enum": [
                        "valuation",
                        "profitability",
                        "liquidity",
                        "solvency",
                        "efficiency",
                        "all",
                    ],
                    "description": "Sub-category of ratios (only applies if analysis_type is 'ratios')",
                    "default": "valuation",
                },
                "quarterly": {
                    "type": "boolean",
                    "description": "Use quarterly data instead of annual",
                    "default": False,
                },
                "years_history": {
                    "type": "integer",
                    "description": "Number of years of historical data to analyze (max 10 for free tier)",
                    "default": 3,
                },
                "growth_rate": {
                    "type": "number",
                    "description": "DCF only: annual free-cash-flow growth over the forecast (e.g. 0.12). Defaults to an assumption.",
                },
                "perpetual_growth_rate": {
                    "type": "number",
                    "description": "DCF only: terminal growth (e.g. 0.025). Defaults to 0.025.",
                },
                "wacc": {
                    "type": "number",
                    "description": "DCF only: discount rate (e.g. 0.096). Defaults to an assumption.",
                },
            },
            "required": ["tickers"],
        }

    async def execute(
        self,
        tickers: Union[str, List[str]],
        analysis_type: str = "ratios",
        sub_type: str = "valuation",
        quarterly: bool = False,
        years_history: int = 3,
        growth_rate: Optional[float] = None,
        perpetual_growth_rate: Optional[float] = None,
        wacc: Optional[float] = None,
        **kwargs,
    ) -> ToolResult:
        Toolkit = _try_import_toolkit()
        if not Toolkit:
            return ToolResult(
                success=False,
                data=None,
                error="financetoolkit library not installed. Run: pip install financetoolkit",
            )

        if isinstance(tickers, str):
            tickers = [t.strip() for t in tickers.split(",") if t.strip()]

        if not tickers:
            return ToolResult(
                success=False, data=None, error="No valid tickers provided."
            )

        settings = get_settings()
        api_key = settings.FMP_API_KEY

        # Calculate a simple start date based on years_history
        from datetime import datetime, timedelta

        start_date = (datetime.now() - timedelta(days=365 * years_history)).strftime(
            "%Y-%m-%d"
        )

        def _run_sync() -> ToolResult:
            # FinanceToolkit performs blocking HTTP + pandas work.
            # Initialize Toolkit without local caching to prevent Uvicorn hot-reloads
            toolkit = Toolkit(
                tickers=tickers,
                api_key=api_key or "",
                quarterly=quarterly,
                start_date=start_date,
                use_cached_data=False,
            )

            result_data = {}

            if analysis_type == "ratios":
                if sub_type == "valuation":
                    ratios_df = toolkit.ratios.collect_valuation_ratios()
                elif sub_type == "profitability":
                    ratios_df = toolkit.ratios.collect_profitability_ratios()
                elif sub_type == "liquidity":
                    ratios_df = toolkit.ratios.collect_liquidity_ratios()
                elif sub_type == "solvency":
                    ratios_df = toolkit.ratios.collect_solvency_ratios()
                elif sub_type == "efficiency":
                    ratios_df = toolkit.ratios.collect_efficiency_ratios()
                else:
                    return ToolResult(
                        success=False,
                        data=None,
                        error=f"Invalid ratio sub_type: {sub_type}",
                    )

                # Convert multi-index DF to dictionary format
                # Usually columns are ('ReturnOnEquity', 'AAPL') or similar depending on transposition
                # We'll just dump to string representation if it's too complex, or to_dict
                try:
                    result_data = ratios_df.to_dict()
                except:
                    result_data = {"raw_data": ratios_df.to_json()}

            elif analysis_type == "dcf":
                # Assuming simple growth rates for the intrinsic valuation out out of box
                # In a real scenario, these could be parameters
                # The library requires all three inputs. Anything the caller did not supply is an
                # explicit, reported assumption rather than a hidden default.
                assumed = {}
                g_rate, p_rate, d_rate = growth_rate, perpetual_growth_rate, wacc
                if g_rate is None:
                    g_rate, assumed["growth_rate"] = 0.08, "default 8% (not supplied)"
                if p_rate is None:
                    p_rate, assumed["perpetual_growth_rate"] = 0.025, "default 2.5% (not supplied)"
                if d_rate is None:
                    d_rate, assumed["wacc"] = 0.10, "default 10% (not supplied)"
                dcf_df = toolkit.models.get_intrinsic_valuation(
                    growth_rate=g_rate,
                    perpetual_growth_rate=p_rate,
                    weighted_average_cost_of_capital=d_rate,
                )
                result_data = dcf_df.to_dict()
                result_data["_inputs"] = {
                    "growth_rate": g_rate, "perpetual_growth_rate": p_rate, "wacc": d_rate, "assumed": assumed,
                }

            elif analysis_type == "wacc":
                wacc_df = toolkit.models.get_weighted_average_cost_of_capital()
                result_data = wacc_df.to_dict()

            elif analysis_type == "dupont":
                dupont_df = toolkit.models.get_extended_dupont_analysis()
                result_data = dupont_df.to_dict()

            elif analysis_type == "fundamentals":
                is_df = toolkit.get_income_statement()
                result_data = {
                    "income_statement_available_years": (
                        list(is_df.columns) if hasattr(is_df, "columns") else []
                    ),
                    "note": "Full fundamentals are very large. Use specific ratios to query specific metrics.",
                }

            else:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"Invalid analysis_type: {analysis_type}",
                )

            return ToolResult(success=True, data=result_data)
        try:
            return await asyncio.to_thread(_run_sync)

        except Exception as e:
            self.logger.error("finance_toolkit_failed", error=str(e))
            error_msg = str(e)
            if "Invalid API Key" in error_msg or "apikey" in error_msg.lower():
                error_msg += " (Check your FMP API Key in Settings)"
            return ToolResult(
                success=False, data=None, error=f"FinanceToolkit error: {error_msg}"
            )
