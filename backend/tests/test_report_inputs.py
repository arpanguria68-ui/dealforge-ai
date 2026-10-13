from app.core.reports.document_workflow import collect_risk_register
from app.core.reports.evidence_brief import build_evidence_brief
from app.core.reports.report_generator import _report_currency

CURATOR = {"agent_type": "data_curator", "success": True, "data": {
    "curated_deal_metrics": {"unit": "Millions", "financials": {
        "2024": {"revenue": 405.91, "net_income": -61.97},
        "2025": {"revenue": 457.55, "operating_income": -43.45, "gross_profit": 354.85}}},
    "source_citations": {"sec": "SEC EDGAR XBRL API via fetch_financial_statements for Acme, Inc. (CIK: 0001517375)"}}}


def test_data_curator_statements_become_sourced_data_points():
    brief = build_evidence_brief({}, [CURATOR])
    revenue = [p for p in brief["data_points"] if p["metric"] == "revenue"]
    assert {p["period"]: p["value"] for p in revenue} == {"2024": 405_910_000.0, "2025": 457_550_000.0}
    assert all(p["basis"] == "reported_source_linked" and p["source_id"] == "S1" for p in revenue)
    assert brief["sources"][0]["url"].endswith("CIK=0001517375&type=10-K")


def test_curated_statements_without_a_filer_citation_stay_unverified():
    bare = {"agent_type": "data_curator", "success": True, "data": {
        "curated_deal_metrics": {"unit": "Millions", "financials": {"2025": {"revenue": 10}}}}}
    point = build_evidence_brief({}, [bare])["data_points"][0]
    assert point["value"] == 10_000_000 and point["source_id"] is None and point["basis"] == "unverified_no_source"


def test_risk_mitigation_lists_are_sentences_not_python_lists():
    result = {"agent_type": "risk_assessor", "success": True, "data": {"risks": [
        {"description": "Bundling pressure", "severity": "critical",
         "mitigation": ["Focus on mid-market.", "Form partnerships."], "evidence": ["Adobe 22% share"]}]}}
    entry = collect_risk_register([result], [])[0]
    assert entry["mitigation"] == "Focus on mid-market. Form partnerships."
    assert "[" not in entry["mitigation"] and entry["evidence"] == "Adobe 22% share"


def test_currency_formatting_puts_the_sign_first_and_capitalises_units():
    assert _report_currency(-43_450_000) == "-$43.45m"
    assert _report_currency(457_550_000) == "$457.55m"
    assert _report_currency(1_210_000_000) == "$1.2bn"
    assert _report_currency(950) == "$950"


def test_latex_in_model_output_is_stripped_but_plain_prices_are_not():
    from app.core.text_clean import strip_latex

    assert strip_latex(r"$\$457.55$") == "$457.55"
    assert strip_latex(r"Metric ($\text{\$M}$)") == "Metric ($M)"
    assert strip_latex(r"Margin of 12\% and $\approx$ 3") == "Margin of 12% and \u2248 3"
    assert strip_latex("costs $5 and $10") == "costs $5 and $10"
    assert strip_latex("plain") == "plain"


def test_period_labels_are_deduplicated_across_fy_prefixes():
    from app.core.reports.document_planner import _deterministic_synthesis

    points = [{"period": p, "value": 1} for p in ("2023", "FY2023", "FY2024", "2025", "FY2025")]
    text = _deterministic_synthesis({"data_points": points})
    assert text["executive_summary"]["situation"] == "Financial data points are recorded for 2023, 2024, 2025."


def test_workbook_lbo_rows_come_from_any_agents_recorded_lbo():
    from app.core.reports.report_generator import _lbo_rows

    results = [{"agent_type": "dcf_lbo_architect", "data": {"lbo_model_summary": {
        "transaction_structure": {"proposed_entry_ev": 725_000_000, "purchase_price_premium_pct": 24.52},
        "exit_returns": {"sponsor_irr_pct": 21.45, "sponsor_moic": 3.15}}}}]
    rows = dict(_lbo_rows(results))
    assert rows["Proposed Entry Ev"] == 725_000_000 and rows["Sponsor Moic"] == 3.15
    assert rows["Transaction Structure"] is None            # section heading
    assert _lbo_rows([{"agent_type": "risk_assessor", "data": {"risks": []}}]) == []


def test_pipeline_data_gap_flags_are_not_listed_as_risks():
    results = [{"agent_type": "red_team", "success": True, "data": {"risks": [
        {"description": "risk_assessor is missing: risk_categories, risk_matrix", "severity": "high"},
        {"description": "Customer concentration in enterprise accounts", "severity": "high"}]}}]
    assert [r["risk"] for r in collect_risk_register(results, [])] == ["Customer concentration in enterprise accounts"]


def test_finding_identifiers_yield_to_the_sentence_beside_them():
    from app.core.reports.evidence_brief import _strings

    item = {"finding": "revenue_cagr", "text": "Revenue grew at a 24.93% CAGR from 2021 to 2025."}
    assert list(_strings(item)) == ["Revenue grew at a 24.93% CAGR from 2021 to 2025."]
    assert list(_strings({"finding": "revenue_cagr"})) == ["revenue_cagr"]   # nothing better: keep it


def test_memo_recommendation_is_read_from_the_memo_line_only():
    from app.core.reports.report_generator import _memo_recommendation

    memo = "### Final Recommendation\n* **RECOMMENDATION:** **BUY** (Subject to confirmatory diligence).\n\n## 2. OVERVIEW"
    assert _memo_recommendation([{"data": {"memo": memo}}]) == "BUY (Subject to confirmatory diligence)."
    assert _memo_recommendation([{"data": {"memo": "No verdict here"}}]) == ""
    assert _memo_recommendation([{"data": {}}]) == ""


def test_same_figure_from_two_agents_and_period_spellings_is_one_data_point():
    both = [
        {"agent_type": "financial_analyst", "success": True, "data": {"historical_financials": [
            {"fiscal_year": "FY2025", "revenue": 457_547_000, "source_url": "https://sec.gov/a"}],
            "sources": [{"url": "https://sec.gov/a"}]}},
        CURATOR,
    ]
    revenue = [p for p in build_evidence_brief({}, both)["data_points"] if p["metric"] == "revenue" and "2025" in p["period"]]
    assert len(revenue) == 1 and revenue[0]["source_id"]


def test_football_field_labels_lead_with_the_currency_sign():
    from app.core.reports.infographic_engine import InfographicEngine, _unit_label

    assert _unit_label(1.2107, "$B") == "$1.21B" and _unit_label(725.0, "$M") == "$725.0M"
    png = InfographicEngine.football_field_chart(
        [{"method": "DCF", "low": 0.61, "mid": 0.70, "high": 0.82}, {"method": "Comps", "low": 0.88, "mid": 1.21, "high": 2.27}],
        reference_lines=[{"label": "Market EV", "value": 0.58}, {"label": "LBO entry", "value": 0.73}])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


def test_recommendation_text_never_presents_a_progress_note_as_a_verdict():
    from app.core.reports.report_generator import _recommendation_text

    memo = [{"data": {"memo": "* **RECOMMENDATION:** **BUY** (Subject to diligence)."}}]
    text, recorded = _recommendation_text({"final_recommendation": "10/10 tasks completed; deal not scored."}, memo)
    assert not recorded and text.startswith("IC memo draft: BUY") and "not an approved decision" in text
    assert _recommendation_text({"final_recommendation": "PASS"}, memo) == ("PASS", True)
    assert _recommendation_text({}, []) == ("No recommendation recorded; human review required.", False)
