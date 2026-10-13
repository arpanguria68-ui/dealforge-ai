import asyncio
import json

from app.core.reports.report_editor import MAX_POINTS, deterministic_edit, edit_report, section_for

RESULTS = [
    {"agent_type": "financial_analyst", "data": {
        "revenue_analysis": {"annual_revenue": 457_547_000, "growth_rate": 12.72},
        "cash_flow": {"free_cash_flow": 39_321_000},
        "key_findings": [f"Finding number {i} about margins and retention trends" for i in range(12)]}},
    {"agent_type": "market_researcher", "data": {
        "market_size": {"tam": 18_000_000_000}, "revenue_analysis": {"annual_revenue": 457_547_000}}},
    {"agent_type": "valuation_agent", "data": {"valuations": {"dcf": {"value": 713_575_460}}}},
]


def test_deterministic_editor_caps_and_deduplicates():
    edit = deterministic_edit(RESULTS)
    assert all(len(sec["points"]) <= MAX_POINTS for sec in edit["sections"])
    fa, mr = section_for(edit, "financial_analyst"), section_for(edit, "market_researcher")
    assert any("$457.55M" in p for p in fa)
    assert not any("$457.55M" in p for p in mr)          # same fact not repeated under another agent
    assert any("TAM" in p for p in mr)


def test_llm_points_with_invented_figures_are_dropped():
    reply = {"key_messages": ["Revenue reached $457.55M in FY2025.", "Net cash is $65M."],
             "sections": [{"agent": "financial_analyst", "points": ["Revenue grew 12.72%.", "Retention is 110%."]},
                          {"agent": "not_an_agent", "points": ["x"]}]}

    async def fake_llm(prompt, system):
        assert "valuation_agent" in prompt and "ONLY" in system
        return "```json\n" + json.dumps(reply) + "\n```"

    edit = asyncio.run(edit_report(RESULTS, {}, llm_call=fake_llm))
    assert edit["editor"] == "llm"
    assert edit["key_messages"] == ["Revenue reached $457.55M in FY2025."]
    assert section_for(edit, "financial_analyst") == ["Revenue grew 12.72%."]
    assert {"Net cash is $65M.", "Retention is 110%."} <= set(edit["dropped_ungrounded"])
    # agents the model skipped keep the rule-based selection rather than vanishing
    assert section_for(edit, "valuation_agent")


def test_editor_falls_back_when_the_model_fails():
    async def broken(prompt, system):
        raise RuntimeError("provider down")

    edit = asyncio.run(edit_report(RESULTS, {}, llm_call=broken))
    assert edit["editor"] == "deterministic" and "provider down" in edit["editor_error"]
    assert section_for(edit, "financial_analyst")


def test_one_section_per_agent_and_memo_cannot_ground_itself():
    results = RESULTS + [
        {"agent_type": "valuation_agent", "data": {"valuations": {"comparable_companies": {"value": 1_186_876_918}}}},
        {"agent_type": "investment_memo_agent", "data": {"memo": "Net cash of approximately $65M. EV $713.58M."}},
    ]
    edit = deterministic_edit(results)
    assert [s["agent"] for s in edit["sections"]].count("valuation_agent") == 1
    assert any("$1.19B" in p for p in section_for(edit, "valuation_agent"))

    async def fake_llm(prompt, system):
        return json.dumps({"key_messages": [], "sections": [
            {"agent": "investment_memo_agent", "points": ["Net cash is about $65M.", "DCF EV is $713.58M."]}]})

    llm_edit = asyncio.run(edit_report(results, {}, llm_call=fake_llm))
    assert section_for(llm_edit, "investment_memo_agent") == ["DCF EV is $713.58M."]


def test_metadata_lines_codes_and_mojibake_do_not_reach_the_brief():
    from app.core.reports.docx_rendering import clean

    assert clean("Leadership \u00e2\u20ac\u201d strong") == "Leadership \u2014 strong"
    results = [{"agent_type": "investment_memo_agent", "data": {"memo": "Deal ID: 123 | Date: today\n\n## Thesis\n- Revenue $457.55M grew 12.72%."}}]
    edit = deterministic_edit(results, evidence={"findings": [{"text": "revenue_cagr"}]})
    assert not any("Deal ID" in p for p in section_for(edit, "investment_memo_agent"))
    assert "revenue_cagr" not in edit["key_messages"] and edit["key_messages"]
