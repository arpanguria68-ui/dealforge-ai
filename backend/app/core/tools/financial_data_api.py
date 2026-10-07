"""
OFAS Financial Data API — Fetch Financial Statements from Public Sources

MCP Tool: fetch_financial_statements
- SEC EDGAR XBRL API (primary, free, no API key)
- Yahoo Finance fallback (via yfinance, free, no API key)

Both sources are free and require NO API keys.
"""

import json
import os
import re
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
import structlog

from app.core.tools.tool_router import BaseTool, ToolResult

logger = structlog.get_logger()

# SEC EDGAR API base URL (free, no API key needed)
SEC_EDGAR_BASE = "https://data.sec.gov"
SEC_WWW_BASE = "https://www.sec.gov"
SEC_COMPANY_TICKERS = f"{SEC_WWW_BASE}/files/company_tickers_exchange.json"
SEC_COMPANY_FACTS = f"{SEC_EDGAR_BASE}/api/xbrl/companyfacts"

# Required headers for SEC EDGAR (must identify the software and provide a contact email)
SEC_HEADERS = {
    "User-Agent": os.environ.get("SEC_USER_AGENT", "DealForge-OFAS/1.0 (contact@dealforge.ai)"),
    "Accept-Encoding": "gzip, deflate",
}
_COMPANY_TICKERS_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "rows": []}

# XBRL taxonomy mappings (Prioritized order: most common/modern first)
XBRL_INCOME_STATEMENT = {
    # Modern Revenue fields (ASC 606)
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue",
    # Legacy/General Revenue fields
    "Revenues": "revenue",
    "SalesRevenueNet": "revenue",
    "TotalRevenues": "revenue",
    "CostOfRevenue": "cost_of_revenue",
    "CostOfGoodsAndServicesSold": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    "OperatingExpenses": "operating_expenses",
    "OperatingIncomeLoss": "operating_income",
    "InterestExpense": "interest_expense",
    "IncomeTaxExpenseBenefit": "income_tax",
    "NetIncomeLoss": "net_income",
    "EarningsPerShareBasic": "eps_basic",
    "EarningsPerShareDiluted": "eps_diluted",
    "WeightedAverageNumberOfSharesOutstandingBasic": "shares_basic",
    "WeightedAverageNumberOfDilutedSharesOutstanding": "shares_diluted",
}

XBRL_BALANCE_SHEET = {
    "Assets": "total_assets",
    "AssetsCurrent": "current_assets",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": "cash",
    "Liabilities": "total_liabilities",
    "LiabilitiesCurrent": "current_liabilities",
    "LongTermDebt": "long_term_debt",
    "LongTermDebtNoncurrent": "long_term_debt",
    "StockholdersEquity": "shareholders_equity",
    "CommonStockSharesOutstanding": "shares_outstanding",
}

XBRL_CASH_FLOW = {
    "NetCashProvidedByUsedInOperatingActivities": "cfo",
    "NetCashProvidedByUsedInInvestingActivities": "cfi",
    "NetCashProvidedByUsedInFinancingActivities": "cff",
    "DepreciationDepletionAndAmortization": "dna",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capex",
    "PaymentOfDividends": "dividends",
    "PaymentsForRepurchaseOfCommonStock": "buybacks",
}


def _try_import_requests():
    """Lazy import requests to avoid startup dependency"""
    try:
        import requests

        return requests
    except ImportError:
        return None


def _try_import_yfinance():
    """Lazy import yfinance for Yahoo Finance fallback"""
    try:
        import yfinance as yf

        return yf
    except ImportError:
        return None


class FetchFinancialStatementsTool(BaseTool):
    """
    Fetch historical financial statements from SEC EDGAR (XBRL API).

    Falls back to Yahoo Finance if EDGAR data is unavailable.
    Both sources are FREE and require NO API keys.
    """

    def __init__(self):
        super().__init__(
            name="fetch_financial_statements",
            description=(
                "Retrieve historical financial statements (income statement, "
                "balance sheet, cash flow) from SEC EDGAR XBRL API. "
                "Free, no API key required. Falls back to Yahoo Finance."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., 'MSFT', 'AAPL')",
                },
                "statements": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["income", "balance", "cashflow"],
                    },
                    "description": "Which statements to fetch",
                },
                "periods": {
                    "type": "integer",
                    "description": "Number of annual periods to fetch (default: 5)",
                    "default": 5,
                },
                "frequency": {
                    "type": "string",
                    "enum": ["annual", "quarterly"],
                    "description": "Annual or quarterly data",
                    "default": "annual",
                },
            },
            "required": ["ticker"],
        }

    async def execute(
        self,
        ticker: str = "",
        statements: Optional[List[str]] = None,
        periods: int = 5,
        frequency: str = "annual",
        **kwargs,
    ) -> ToolResult:
        """Fetch financial statements — try SEC EDGAR first, then Yahoo Finance.

        Now async (matching BaseTool contract). Blocking I/O is delegated to a thread.
        """
        import asyncio

        statements = statements or ["income", "balance", "cashflow"]
        identifier = str(kwargs.get("company_name") or kwargs.get("identifier") or ticker).strip()
        ticker = identifier.upper()

        if not ticker:
            return ToolResult(
                success=False,
                data=None,
                error="Ticker symbol is required",
            )

        if re.sub(r"[^A-Z0-9]", "", ticker) in {
            "TARGET", "TARGETCOMPANY", "THETARGET", "UNKNOWN", "COMPANY", "PUBLICCOMPANY"
        }:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    "A specific ticker or issuer name is required. A generic placeholder "
                    "cannot be resolved to a public company."
                ),
            )

        # Try SEC EDGAR first (blocking I/O → thread)
        result = await asyncio.to_thread(
            self._fetch_from_edgar, ticker, statements, periods, frequency
        )
        if result and result.get("has_data"):
            return ToolResult(
                success=True,
                data={
                    "source": "sec_edgar",
                    "currency": "USD",
                    **result,
                },
            )

        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", ticker):
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"SEC EDGAR could not resolve public company '{identifier}'. "
                    "Private-company financials require supplied documents or a licensed data source."
                ),
            )

        # Fallback to Yahoo Finance (blocking I/O → thread); keep its source classification explicit.
        logger.info("SEC EDGAR unavailable, trying Yahoo Finance", ticker=ticker)
        result = await asyncio.to_thread(
            self._fetch_from_yfinance, ticker, statements, periods, frequency
        )
        if result and result.get("has_data"):
            return ToolResult(
                success=True,
                data={
                    "source": "yahoo_finance",
                    "ticker": ticker,
                    "currency": "USD",
                    "source_notice": "Yahoo Finance vendor data; not a primary filing source.",
                    "retrieved_at": datetime.utcnow().isoformat() + "Z",
                    **result,
                },
            )

        return ToolResult(
            success=False,
            data=None,
            error=f"Could not retrieve financial statements for {identifier} from SEC EDGAR or the configured secondary source.",
        )

    def _fetch_from_edgar(
        self,
        ticker: str,
        statements: List[str],
        periods: int,
        frequency: str,
    ) -> Optional[Dict]:
        """Fetch from SEC EDGAR XBRL API"""
        requests = _try_import_requests()
        if not requests:
            return None

        try:
            # Step 1: Resolve exact ticker or normalized company name to a SEC CIK.
            tickers_data = _COMPANY_TICKERS_CACHE.get("rows")
            if not tickers_data or time.monotonic() - _COMPANY_TICKERS_CACHE["loaded_at"] > 86400:
                resp = requests.get(SEC_COMPANY_TICKERS, headers=SEC_HEADERS, timeout=10)
                resp.raise_for_status()
                payload = resp.json()
                fields = payload.get("fields", [])
                if isinstance(payload.get("data"), list) and fields:
                    tickers_data = [dict(zip(fields, row)) for row in payload["data"]]
                else:
                    tickers_data = list(payload.values()) if isinstance(payload, dict) else []
                _COMPANY_TICKERS_CACHE.update(rows=tickers_data, loaded_at=time.monotonic())

            def normalized_name(value: str) -> str:
                name = re.sub(r"[^A-Z0-9]", "", value.upper())
                for suffix in ("CORPORATION", "INCORPORATED", "LIMITED", "COMPANY", "CORP", "INC", "LTD", "LLC", "PLC", "CO"):
                    if name.endswith(suffix) and len(name) > len(suffix) + 2:
                        return name[:-len(suffix)]
                return name

            matches = []
            for entry in tickers_data:
                if not isinstance(entry, dict):
                    continue
                symbol = str(entry.get("ticker", "")).upper()
                title = str(entry.get("name") or entry.get("title") or "")
                cik_value = entry.get("cik") or entry.get("cik_str")
                if symbol == ticker or normalized_name(title) == normalized_name(ticker):
                    matches.append((str(cik_value).zfill(10), symbol, title, entry.get("exchange")))

            if len(matches) != 1:
                logger.warning("Company identifier not uniquely resolved in SEC EDGAR", identifier=ticker, matches=len(matches))
                return None
            cik, resolved_ticker, resolved_name, exchange = matches[0]

            # Step 2: Fetch company facts (XBRL data)
            facts_url = f"{SEC_COMPANY_FACTS}/CIK{cik}.json"
            resp = requests.get(facts_url, headers=SEC_HEADERS, timeout=30)
            resp.raise_for_status()
            facts = resp.json()

            us_gaap = facts.get("facts", {}).get("us-gaap", {})
            if not us_gaap:
                return None

            result = {
                "has_data": True,
                "cik": cik,
                "entity_name": facts.get("entityName", resolved_name),
                "ticker": resolved_ticker,
                "exchange": exchange,
                "source": "sec_edgar_companyfacts",
                "source_url": facts_url,
                "retrieved_at": datetime.utcnow().isoformat() + "Z",
            }

            # Step 3: Extract requested statements
            if "income" in statements:
                result["income_statement"] = self._extract_xbrl_items(
                    us_gaap, XBRL_INCOME_STATEMENT, periods, frequency, cik=cik
                )

            if "balance" in statements:
                result["balance_sheet"] = self._extract_xbrl_items(
                    us_gaap, XBRL_BALANCE_SHEET, periods, frequency, cik=cik
                )

            if "cashflow" in statements:
                result["cash_flow"] = self._extract_xbrl_items(
                    us_gaap, XBRL_CASH_FLOW, periods, frequency, cik=cik
                )

            # Extract fiscal years covered
            fiscal_years = set()
            for stmt in ["income_statement", "balance_sheet", "cash_flow"]:
                if stmt in result:
                    for name, item_data in result[stmt].items():
                        if name == "_sources":
                            continue
                        if isinstance(item_data, dict):
                            fiscal_years.update(item_data.keys())
            result["fiscal_years"] = sorted(fiscal_years)[-periods:]

            return result

        except Exception as e:
            logger.warning("SEC EDGAR fetch failed", ticker=ticker, error=str(e))
            return None

    def _extract_xbrl_items(
        self,
        us_gaap: Dict,
        field_map: Dict[str, str],
        periods: int,
        frequency: str,
        cik: str = "",
    ) -> Dict[str, Any]:
        """Extract standardized financial items from XBRL data"""
        result = {}
        sources = {}
        annual_forms = {"10-K", "20-F", "40-F"}
        quarter_forms = {"10-Q"}

        for xbrl_field, std_name in field_map.items():
            if xbrl_field not in us_gaap:
                continue

            field_data = us_gaap[xbrl_field]
            units = field_data.get("units", {})

            values = next((units[key] for key in ("USD", "USD/shares", "shares", "pure") if key in units), [])

            if not values:
                continue

            if std_name not in result:
                result[std_name] = {}
                sources[std_name] = {}

            # Filter by form type and extract yearly values
            for entry in values:
                form = entry.get("form", "")
                allowed_forms = annual_forms if frequency == "annual" else quarter_forms
                if form not in allowed_forms:
                    continue

                end_date = entry.get("end", "")
                if not end_date:
                    continue

                start_date = entry.get("start")
                is_instant = std_name in XBRL_BALANCE_SHEET.values()
                if start_date:
                    try:
                        duration_days = (
                            datetime.strptime(end_date, "%Y-%m-%d")
                            - datetime.strptime(start_date, "%Y-%m-%d")
                        ).days
                    except ValueError:
                        continue
                    if frequency == "annual" and not 330 <= duration_days <= 400:
                        continue
                    if frequency != "annual" and not 70 <= duration_days <= 110:
                        continue
                elif not is_instant:
                    continue

                year = end_date[:4]
                if frequency != "annual":
                    end = datetime.strptime(end_date, "%Y-%m-%d")
                    year = f"{end.year}-Q{(end.month - 1) // 3 + 1}"
                val = entry.get("val")

                # Data prioritized by end_date (newest first)
                # If we already have data for this year, only overwrite if this field is 'better'
                # or if the date is actually more recent for the same fiscal year
                current_entry = result[std_name].get(year)
                filed = entry.get("filed", "")
                current_filed = sources[std_name].get(year, {}).get("filed", "")
                reported_fy = entry.get("fy")
                current_reported_fy = sources[std_name].get(year, {}).get("reported_fy")
                try:
                    is_fiscal_year_filing = int(reported_fy) == int(year) and entry.get("fp") == "FY"
                except (TypeError, ValueError):
                    is_fiscal_year_filing = False
                try:
                    current_is_fiscal_year_filing = (
                        int(current_reported_fy) == int(year)
                        and sources[std_name].get(year, {}).get("fiscal_period") == "FY"
                    )
                except (TypeError, ValueError):
                    current_is_fiscal_year_filing = False
                if (
                    current_entry is None
                    or (is_fiscal_year_filing and not current_is_fiscal_year_filing)
                    or (is_fiscal_year_filing == current_is_fiscal_year_filing and filed > current_filed)
                ):
                    result[std_name][year] = val
                    accession = str(entry.get("accn", ""))
                    accession_path = accession.replace("-", "")
                    sources[std_name][year] = {
                        "form": form, "filed": filed, "start": start_date,
                        "end": end_date, "accession": accession,
                        "reported_fy": reported_fy, "fiscal_period": entry.get("fp"),
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/" if cik and accession_path else None,
                    }

        # Post-process: limit to requested periods and sort years
        processed_result = {}
        for std_name, years_data in result.items():
            sorted_years = sorted(years_data.keys(), reverse=True)[:periods]
            time_series = {y: years_data[y] for y in sorted(sorted_years)}
            if time_series:
                processed_result[std_name] = time_series

        processed_result["_sources"] = {
            metric: {
                period: metadata for period, metadata in period_map.items()
                if period in processed_result.get(metric, {})
            }
            for metric, period_map in sources.items()
        }

        return processed_result

    def _fetch_from_yfinance(
        self,
        ticker: str,
        statements: List[str],
        periods: int,
        frequency: str,
    ) -> Optional[Dict]:
        """Fallback: fetch from Yahoo Finance via yfinance"""
        yf = _try_import_yfinance()
        if not yf:
            return None

        try:
            stock = yf.Ticker(ticker)
            result = {"has_data": False}

            if "income" in statements:
                if frequency == "annual":
                    df = stock.income_stmt
                else:
                    df = stock.quarterly_income_stmt

                if df is not None and not df.empty:
                    result["income_statement"] = self._df_to_dict(df, periods)
                    result["has_data"] = True

            if "balance" in statements:
                if frequency == "annual":
                    df = stock.balance_sheet
                else:
                    df = stock.quarterly_balance_sheet

                if df is not None and not df.empty:
                    result["balance_sheet"] = self._df_to_dict(df, periods)
                    result["has_data"] = True

            if "cashflow" in statements:
                if frequency == "annual":
                    df = stock.cashflow
                else:
                    df = stock.quarterly_cashflow

                if df is not None and not df.empty:
                    result["cash_flow"] = self._df_to_dict(df, periods)
                    result["has_data"] = True

            if result["has_data"]:
                # Extract fiscal years
                fiscal_years = set()
                for key in ["income_statement", "balance_sheet", "cash_flow"]:
                    if key in result:
                        for item_data in result[key].values():
                            if isinstance(item_data, dict):
                                fiscal_years.update(item_data.keys())
                result["fiscal_years"] = sorted(fiscal_years)[-periods:]

            return result

        except Exception as e:
            logger.warning("Yahoo Finance fetch failed", ticker=ticker, error=str(e))
            return None

    def _df_to_dict(self, df, periods: int) -> Dict[str, Dict[str, Any]]:
        """Convert a pandas DataFrame (from yfinance) to standardized dict"""
        result = {}
        # Columns are dates, rows are line items
        cols = list(df.columns)[:periods]

        for idx, row in df.iterrows():
            item_name = str(idx).replace(" ", "_").lower()
            time_series = {}
            for col in cols:
                year = str(col.year) if hasattr(col, "year") else str(col)[:4]
                val = row[col]
                if val is not None and str(val) != "nan":
                    try:
                        time_series[year] = float(val)
                    except (ValueError, TypeError):
                        time_series[year] = val

            if time_series:
                result[item_name] = time_series

        return result
