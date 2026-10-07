from types import SimpleNamespace

import pytest


def test_input_only_fiscal_year_sentence_extracts_and_derives_growth():
    from app.agents.financial_analyst import FinancialAnalystAgent

    task = (
        "Use only the supplied figures for this fictional test company; do not fetch external data. "
        "FY2024 revenue was $50 million and EBITDA was $5 million. "
        "FY2025 revenue was $55 million and EBITDA was $6 million."
    )
    result = FinancialAnalystAgent.__new__(FinancialAnalystAgent)._provided_metrics_assessment(task)

    assert result["revenue_analysis"]["annual_revenue"] == 55_000_000
    assert result["revenue_analysis"]["growth_rate"] == 10
    assert result["profitability"]["ebitda"] == 6_000_000
    assert result["profitability"]["ebitda_margin"] == pytest.approx(10.91)
    assert result["derived_growth"]["ebitda_yoy_percent"] == 20
    assert result["historical_financials"] == [
        {"period": "FY2024", "source_type": "user_supplied_unverified", "revenue": 50_000_000, "ebitda": 5_000_000},
        {"period": "FY2025", "source_type": "user_supplied_unverified", "revenue": 55_000_000, "ebitda": 6_000_000},
    ]


def test_request_to_avoid_unsupported_valuation_does_not_disable_sec_retrieval():
    from app.agents.financial_analyst import FinancialAnalystAgent

    task = "Analyze MSFT using SEC 10-Ks; do not estimate EBITDA or valuation; show unknowns."
    assert not FinancialAnalystAgent.is_input_only_request(task)


def test_fiscal_year_count_is_parsed_from_filing_request():
    from app.agents.financial_analyst import FinancialAnalystAgent

    assert FinancialAnalystAgent._requested_fiscal_year_count(
        "Report the latest four annual SEC 10-K filings."
    ) == 4


@pytest.mark.asyncio
async def test_unparseable_input_only_task_fails_closed_without_llm(monkeypatch):
    from app.agents.financial_analyst import FinancialAnalystAgent

    agent = FinancialAnalystAgent()

    async def unexpected_llm(*args, **kwargs):
        raise AssertionError("Offline request must not use an LLM")

    monkeypatch.setattr(agent, "generate_with_tools", unexpected_llm)
    result = await agent.run("Use only supplied figures; do not fetch external data.")

    assert not result.success
    assert result.data["error"] == "input_metrics_not_found"


@pytest.mark.asyncio
async def test_financial_data_route_awaits_async_tool(monkeypatch):
    from app import main
    from app.core.tools import financial_data_api

    class FakeTool:
        async def execute(self, **kwargs):
            assert kwargs["ticker"] == "MSFT"
            return SimpleNamespace(success=True, data={"source": "sec_edgar", "ticker": "MSFT"}, error=None)

    monkeypatch.setattr(financial_data_api, "FetchFinancialStatementsTool", FakeTool)

    async def body():
        return {"ticker": "MSFT", "periods": 3}

    result = await main.ofas_fetch_financial_data(SimpleNamespace(json=body))
    assert result["ticker"] == "MSFT"


def test_provider_error_payload_is_not_counted_as_analysis():
    from app.core.reports.evidence_brief import build_evidence_brief

    brief = build_evidence_brief(
        {"id": "deal-1"},
        [{"agent_type": "financial_analyst", "success": True,
          "data": {"reasoning": "[Error] No configured provider returned a usable response."}}],
    )

    assert brief["successful_analysis_count"] == 0
    assert brief["findings"] == []


def test_explicit_ticker_wins_over_bad_company_name_guess():
    from app.main import _explicit_public_ticker

    task = "Fetch Microsoft Corporation (MSFT) financial statements for the latest four fiscal years."
    guessed_company_name = "latest four fiscal years"

    assert _explicit_public_ticker(task) == "MSFT"
    assert _explicit_public_ticker(guessed_company_name) is None


@pytest.mark.parametrize(
    "text,ticker",
    [
        ("Assess NVIDIA Corporation (NASDAQ: NVDA).", "NVDA"),
        ("Assess NVIDIA Corporation (NVDA).", "NVDA"),
        ("The ticker is NVDA.", "NVDA"),
        ("The ticker: NVDA.", "NVDA"),
    ],
)
def test_explicit_ticker_parser_handles_exchange_qualified_symbols(text, ticker):
    from app.main import _explicit_public_ticker

    assert _explicit_public_ticker(text) == ticker


@pytest.mark.parametrize(
    "text,company",
    [
        ("investment screen for NVIDIA Corporation (NASDAQ: NVDA)", "NVIDIA Corporation"),
        ("Analyze Microsoft Corporation (MSFT) for the latest four years", "Microsoft Corporation"),
    ],
)
def test_explicit_company_parser_preserves_name_next_to_ticker(text, company):
    from app.main import _explicit_company_name

    assert _explicit_company_name(text) == company


def test_legacy_export_identity_recovers_issuer_and_ticker_from_analysis_prompt():
    from app.main import _deal_identity_for_export

    deal = {"target_company": "Target Company"}
    todo = {
        "company_name": "Target Company",
        "items": [{
            "title": "Focused financial assessment",
            "description": "Screen NVIDIA Corporation (NASDAQ: NVDA) using SEC annual filings.",
        }],
    }

    resolved = _deal_identity_for_export(deal, todo)

    assert resolved["target_company"] == "NVIDIA Corporation"
    assert resolved["ticker"] == "NVDA"
    assert deal["target_company"] == "Target Company"


@pytest.mark.asyncio
@pytest.mark.parametrize("placeholder", ["Target Company", "the target", "Unknown"])
async def test_financial_data_tool_refuses_generic_company_placeholders(placeholder, monkeypatch):
    from app.core.tools.financial_data_api import FetchFinancialStatementsTool

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("A generic placeholder must not be sent to SEC/Yahoo lookup")

    monkeypatch.setattr(FetchFinancialStatementsTool, "_fetch_from_edgar", unexpected_fetch)
    result = await FetchFinancialStatementsTool().execute(ticker=placeholder)

    assert not result.success
    assert "generic placeholder" in result.error


@pytest.mark.asyncio
async def test_unstructured_llm_prose_falls_back_to_structured_sec_facts(monkeypatch):
    from app.agents.financial_analyst import FinancialAnalystAgent
    from app.core.quality.agent_quality_store import AgentQualityStore
    from app.core.tools.financial_data_api import FetchFinancialStatementsTool

    facts = {
        "has_data": True,
        "source": "sec_edgar_companyfacts",
        "ticker": "MSFT",
        "entity_name": "MICROSOFT CORPORATION",
        "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
        "income_statement": {
            "revenue": {"2024": 100, "2025": 120},
            "gross_profit": {"2024": 60, "2025": 72},
            "_sources": {"revenue": {
                "2024": {"form": "10-K", "filed": "2025-07-30", "end": "2024-06-30", "url": "https://www.sec.gov/Archives/edgar/data/1/a/"},
                "2025": {"form": "10-K", "filed": "2026-07-30", "end": "2025-06-30", "url": "https://www.sec.gov/Archives/edgar/data/1/b/"},
            }},
        },
        "balance_sheet": {},
        "cash_flow": {},
    }

    async def no_init(self):
        return None

    async def no_practices(self, *args, **kwargs):
        return []

    async def fetch(self, **kwargs):
        return SimpleNamespace(success=True, data=facts, error=None)

    async def unstructured(self, *args, **kwargs):
        return {"content": "I will now analyze the data and calculate growth.", "tool_results": []}

    monkeypatch.setattr(AgentQualityStore, "initialize", no_init)
    monkeypatch.setattr(AgentQualityStore, "get_historical_best_practices", no_practices)
    monkeypatch.setattr(FetchFinancialStatementsTool, "execute", fetch)
    agent = FinancialAnalystAgent(llm_client=object(), pageindex_client=object(), tool_router=object())
    monkeypatch.setattr(agent, "generate_with_tools", unstructured)

    result = await agent.run(
        "Analyze Microsoft Corporation (MSFT) SEC revenue for the latest fiscal years.",
        context={"ticker": "MSFT"},
    )

    assert result.success
    assert result.data["synthesis_status"] == "source_grounded_fallback"
    assert result.data["revenue_analysis"]["annual_revenue"] == 120
    assert result.data["revenue_analysis"]["growth_rate"] == pytest.approx(20)
    assert result.data["historical_financials"][-1]["revenue"] == 120
    assert result.data["historical_financials"][-1]["period_end_date"] == "2025-06-30"
    assert result.data["historical_financials"][-1]["source_url"].startswith("https://www.sec.gov/Archives/")
    assert result.data["key_findings"]
    assert result.data["key_findings"][0]["finding"] == "revenue_cagr"
    assert result.data["key_findings"][0]["value_percent"] == pytest.approx(20)
    assert result.data["sources"]
    assert result.data["profitability"]["ebitda"] is None


def test_sec_xbrl_extraction_uses_annual_duration_and_keeps_filing_source():
    from app.core.tools.financial_data_api import FetchFinancialStatementsTool

    us_gaap = {
        "Revenues": {"units": {"USD": [
            {"form": "10-K", "start": "2023-07-01", "end": "2024-06-30", "filed": "2024-07-30", "accn": "0000000000-24-000001", "val": 100},
            {"form": "10-K", "start": "2024-10-01", "end": "2024-12-31", "filed": "2025-01-30", "accn": "0000000000-25-000001", "val": 30},
            {"form": "10-K", "start": "2024-07-01", "end": "2025-06-30", "filed": "2025-07-30", "accn": "0000000000-25-000002", "val": 120},
        ]}}
    }
    result = FetchFinancialStatementsTool()._extract_xbrl_items(
        us_gaap, {"Revenues": "revenue"}, 5, "annual", cik="0000000001"
    )

    assert result["revenue"] == {"2024": 100, "2025": 120}
    assert result["_sources"]["revenue"]["2025"]["form"] == "10-K"
    assert "Archives/edgar/data/1/000000000025000002/" in result["_sources"]["revenue"]["2025"]["url"]


def test_sec_period_prefers_its_own_fiscal_year_filing_over_later_comparative():
    from app.core.tools.financial_data_api import FetchFinancialStatementsTool

    us_gaap = {
        "Revenues": {"units": {"USD": [
            {"form": "10-K", "start": "2022-07-01", "end": "2023-06-30", "filed": "2023-07-27", "accn": "0000000000-23-000001", "fy": 2023, "fp": "FY", "val": 100},
            {"form": "10-K", "start": "2022-07-01", "end": "2023-06-30", "filed": "2025-07-30", "accn": "0000000000-25-000001", "fy": 2025, "fp": "FY", "val": 100},
        ]}}
    }

    result = FetchFinancialStatementsTool()._extract_xbrl_items(
        us_gaap, {"Revenues": "revenue"}, 5, "annual", cik="0000000001"
    )

    source = result["_sources"]["revenue"]["2023"]
    assert source["filed"] == "2023-07-27"
    assert source["reported_fy"] == 2023
    assert "000000000023000001" in source["url"]


def test_secondary_vendor_output_uses_vendor_label_and_period_for_cash_flow():
    from app.agents.financial_analyst import FinancialAnalystAgent

    assessment = FinancialAnalystAgent._statement_data_assessment({
        "source": "yahoo_finance",
        "ticker": "MSFT",
        "income_statement": {"total_revenue": {"2025": 100, "2026": 120}},
        "cash_flow": {
            "operating_cash_flow": {"2026": 30},
            "capital_expenditure": {"2026": 8},
        },
        "balance_sheet": {},
    })

    assert assessment["data_source"] == "yahoo_finance"
    assert assessment["revenue_analysis"]["growth_basis"].endswith("yahoo_finance.")
    assert assessment["cash_flow"]["operating_cash_flow"] == 30
    assert assessment["cash_flow"]["fiscal_year"] == "FY2026"
    assert "secondary-vendor" in assessment["data_limitations"][0]
    assert assessment["confidence_basis"] == "not_calibrated_source_report"


def test_free_cash_flow_requires_cfo_and_capex_from_same_fiscal_period():
    from app.agents.financial_analyst import FinancialAnalystAgent

    assessment = FinancialAnalystAgent._statement_data_assessment({
        "source": "sec_edgar_companyfacts", "ticker": "TEST",
        "income_statement": {"revenue": {"2025": 100, "2026": 120}},
        "cash_flow": {
            "cfo": {"2025": 20, "2026": 60},
            "capex": {"2025": 5},
            "_sources": {
                "cfo": {"2025": {"url": "https://sec.test/2025"}, "2026": {"url": "https://sec.test/2026"}},
                "capex": {"2025": {"url": "https://sec.test/2025"}},
            },
        },
        "balance_sheet": {},
    })

    assert assessment["cash_flow"]["operating_cash_flow"] == 60
    assert assessment["cash_flow"]["fiscal_year"] == "FY2026"
    assert assessment["cash_flow"]["capital_expenditures"] is None
    assert assessment["cash_flow"]["capital_expenditures_fiscal_year"] is None
    assert assessment["cash_flow"]["free_cash_flow"] == 15
    assert assessment["cash_flow"]["free_cash_flow_fiscal_year"] == "FY2025"
    assert assessment["cash_flow"]["free_cash_flow_source_urls"] == ["https://sec.test/2025"]


def test_sec_assessment_keeps_net_income_scalar_and_excludes_comparative_sources():
    from app.agents.financial_analyst import FinancialAnalystAgent

    revenue_sources = {
        year: {"form": "10-K", "reported_fy": int(year), "url": f"https://sec.test/{year}"}
        for year in ("2023", "2024", "2025", "2026")
    }
    assessment = FinancialAnalystAgent._statement_data_assessment({
        "source": "sec_edgar_companyfacts", "ticker": "MSFT",
        "income_statement": {
            "revenue": {"2023": 100, "2024": 110, "2025": 120, "2026": 130},
            "net_income": {"2026": 30},
            "_sources": {
                "revenue": revenue_sources,
                "interest_expense": {
                    "2023": {"reported_fy": 2025, "url": "https://sec.test/wrong-comparative"},
                    "2021": {"reported_fy": 2021, "url": "https://sec.test/out-of-scope"},
                },
            },
        },
        "balance_sheet": {}, "cash_flow": {},
    }, period_limit=4)

    assert assessment["profitability"]["net_income"] == 30
    assert {source["url"] for source in assessment["sources"]} == {
        f"https://sec.test/{year}" for year in ("2023", "2024", "2025", "2026")
    }


def test_financial_source_without_standardized_metrics_is_not_reportable():
    from app.agents.financial_analyst import FinancialAnalystAgent

    assessment = FinancialAnalystAgent._statement_data_assessment({
        "source": "yahoo_finance", "ticker": "MSFT",
        "income_statement": {"unknown_vendor_metric": {"2026": 120}},
    })

    assert assessment["historical_financials"] == []
    assert "no usable standardized" in assessment["reasoning"].lower()


def test_evidence_guard_removes_unsupported_profit_cash_and_balance_sheet_values():
    from app.agents.financial_analyst import FinancialAnalystAgent

    context = {"financial_data": {
        "income_statement": {
            "revenue": {"2025": 100, "2026": 120},
            "operating_income": {"2026": 30},
            "net_income": {"2026": 20},
        },
        "cash_flow": {"cfo": {"2025": 10, "2026": 18}, "capex": {"2025": 4, "2026": 7}},
        "balance_sheet": {"cash": {"2026": 12}, "long_term_debt": {"2026": 5}},
    }}
    analysis = {
        "revenue_analysis": {"annual_revenue": 999, "growth_rate": 999},
        "profitability": {"operating_income": 999, "net_income": 999},
        "cash_flow": {"operating_cash_flow": 999, "capital_expenditures": 999,
                      "free_cash_flow": 999, "burn_rate": 999},
        "balance_sheet": {"cash": 999, "long_term_debt": 999, "shareholders_equity": 999},
        "valuation": {"dcf_estimate": 999, "multiple_estimate": 999},
    }

    FinancialAnalystAgent.__new__(FinancialAnalystAgent)._enforce_evidence_grounding(
        analysis, "Analyze revenue and financial statements", context, []
    )

    assert analysis["profitability"]["operating_income"] == 30
    assert analysis["profitability"]["net_income"] == 20
    assert analysis["cash_flow"]["operating_cash_flow"] == 18
    assert analysis["cash_flow"]["capital_expenditures"] == 7
    assert analysis["cash_flow"]["free_cash_flow"] == 11
    assert analysis["cash_flow"]["burn_rate"] is None
    assert analysis["balance_sheet"] == {"cash": 12, "long_term_debt": 5, "shareholders_equity": None}
    assert analysis["valuation"]["dcf_estimate"] is None
