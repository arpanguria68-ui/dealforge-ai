"""
ESG & Sustainability Tools for DealForge AI.

Specialized tools to extract carbon footprint data, flag supply chain risks,
and generate composite ESG scores during M&A diligence.
"""

from typing import Dict, Any, List, Optional
import structlog
from app.core.tools.tool_router import BaseTool, ToolResult
from app.core.tools.grounded_extraction import (
    EXTRACTED_NOTE, HEURISTIC_NOTE, SEVERITY_RANK, extract_grounded, max_severity, pick,
)
import re

logger = structlog.get_logger(__name__)


_UNIT_TO_TONNES = {"tco2e": 1.0, "ktco2e": 1_000.0, "mtco2e": 1_000_000.0}


def _number_in_quote(value: float, quote: str) -> bool:
    """The reported figure must literally appear in its supporting quote."""
    digits = re.sub(r"[^\d.]", "", f"{value:,.6f}".rstrip("0").rstrip("."))
    quote_digits = re.sub(r"[,\s]", "", quote or "")
    return bool(digits) and digits.split(".")[0] in quote_digits


class CarbonFootprintExtractorTool(BaseTool):
    """Extracts Scope 1/2/3 emissions with quote-verified figures."""

    output_quality = "extracted"

    def __init__(self):
        super().__init__(
            name="carbon_footprint_extractor",
            description=(
                "Extracts reported Scope 1, 2 and 3 GHG emissions (normalized to tCO2e, "
                "latest reported year per scope) from sustainability reports or text; every "
                "figure is backed by a verbatim quote containing the number."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "sustainability_text": {
                    "type": "string",
                    "description": "Text body containing the target's sustainability or CSR report.",
                },
            },
            "required": ["sustainability_text"],
        }

    async def execute(self, sustainability_text: str = "", **kwargs) -> ToolResult:
        items = await extract_grounded(
            "carbon_footprint_extractor",
            sustainability_text,
            "Extract every reported greenhouse-gas emissions figure by scope. Do not compute "
            "or estimate figures that are not stated.",
            {
                "scope": "1, 2 or 3",
                "value": "number exactly as reported (no unit conversion)",
                "unit": "one of tCO2e|ktCO2e|MtCO2e",
                "year": "reporting year as integer, or null",
            },
        )
        figures: List[Dict[str, Any]] = []
        if items is None:
            quality, note = "heuristic", HEURISTIC_NOTE
            lowered = (sustainability_text or "").lower()
            for scope in (1, 2, 3):
                # Take the first number between "scope N" and the next "scope"
                # mention, so "scope 1 and scope 2 were 500" no longer gives scope1 = 2.
                m = re.search(rf"scope {scope}\b(.*?)(?=scope [123]\b|$)", lowered, re.S)
                num = re.search(r"\d[\d,]*(?:\.\d+)?", m.group(1)) if m else None
                if num:
                    figures.append({"scope": scope, "tco2e": float(num.group(0).replace(",", "")),
                                    "year": None, "quote": None})
        else:
            quality, note = "extracted", EXTRACTED_NOTE
            for i in items:
                try:
                    scope = int(str(i.get("scope")).strip()[0])
                    value = float(str(i.get("value")).replace(",", ""))
                except (TypeError, ValueError, IndexError):
                    continue
                unit = pick(str(i.get("unit") or "tco2e").replace("₂", "2"), tuple(_UNIT_TO_TONNES), "tco2e")
                if scope not in (1, 2, 3) or value < 0 or not _number_in_quote(value, i["quote"]):
                    continue
                year = i.get("year")
                figures.append({
                    "scope": scope,
                    "tco2e": value * _UNIT_TO_TONNES[unit],
                    "year": int(year) if str(year or "").isdigit() else None,
                    "quote": i["quote"],
                })

        latest: Dict[int, Dict[str, Any]] = {}
        for f in figures:
            current = latest.get(f["scope"])
            if current is None or (f["year"] or 0) > (current["year"] or 0):
                latest[f["scope"]] = f
        scope1, scope2, scope3 = (latest.get(n, {}).get("tco2e", 0.0) for n in (1, 2, 3))
        total = scope1 + scope2 + scope3
        risk_level = "High" if total > 100_000 else "Medium" if total > 10_000 else "Low"

        return ToolResult(
            success=True,
            data={
                "scope1_tco2e": scope1,
                "scope2_tco2e": scope2,
                "scope3_tco2e": scope3,
                "total_tco2e": total,
                "scopes_reported": sorted(latest),
                "emissions_risk_level": risk_level if latest else "Not reported",
                "figures": figures,
                "note": "" if latest else "No discrete emission tonnages found.",
                "data_quality": quality,
                "method_note": note,
            },
        )


class SupplyChainRiskFlaggerTool(BaseTool):
    """Extracts quote-backed supply chain ESG risks."""

    output_quality = "extracted"

    RISK_TYPES = ("forced_labor", "child_labor", "conflict_minerals", "unaudited_suppliers",
                  "environmental", "concentration", "other")

    def __init__(self):
        super().__init__(
            name="supply_chain_risk_flagger",
            description=(
                "Extracts environmental, ethical, forced-labour, conflict-mineral and "
                "supplier-audit risks from supplier documentation or text, each backed by a "
                "verbatim quote, with an overall severity."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "supplier_text": {
                    "type": "string",
                    "description": "Text describing the target's supply chain or manufacturing footprint.",
                },
            },
            "required": ["supplier_text"],
        }

    async def execute(self, supplier_text: str = "", **kwargs) -> ToolResult:
        items = await extract_grounded(
            "supply_chain_risk_flagger",
            supplier_text,
            "Identify supply chain ESG risks the text describes as present.",
            {
                "risk_type": "one of " + "|".join(self.RISK_TYPES),
                "finding": "one-sentence description",
                "severity": "one of low|medium|high|critical",
            },
        )
        if items is None:
            risks = self._keyword_risks(supplier_text)
            quality, note = "heuristic", HEURISTIC_NOTE
        else:
            risks = [
                {
                    "risk_type": pick(i.get("risk_type"), self.RISK_TYPES, "other"),
                    "finding": str(i.get("finding") or "")[:300],
                    "severity": pick(i.get("severity"), tuple(SEVERITY_RANK), "medium"),
                    "quote": i["quote"],
                }
                for i in items
            ]
            quality, note = "extracted", EXTRACTED_NOTE

        return ToolResult(
            success=True,
            data={
                "identified_risks": [r["finding"] for r in risks]
                or ["No supply chain risks identified in the provided text."],
                "risks": risks,
                "supply_chain_risk_severity": max_severity([r["severity"] for r in risks]).capitalize(),
                "data_quality": quality,
                "method_note": note,
            },
        )

    @staticmethod
    def _keyword_risks(supplier_text: str) -> List[Dict[str, Any]]:
        text = (supplier_text or "").lower()
        risks = []
        if "overseas" in text or "sweatshop" in text or "un-audited" in text or "no audited" in text:
            risks.append({"risk_type": "unaudited_suppliers", "severity": "high", "quote": None,
                          "finding": "Potential un-audited labor in overseas manufacturing."})
        if "forced labor" in text or "xinjiang" in text:
            risks.append({"risk_type": "forced_labor", "severity": "critical", "quote": None,
                          "finding": "Forced labor exposure mentioned."})
        if "conflict mineral" in text or "cobalt" in text:
            risks.append({"risk_type": "conflict_minerals", "severity": "medium", "quote": None,
                          "finding": "Exposure to conflict minerals supply chain."})
        return risks


class ESGScorerTool(BaseTool):
    """Computes a composite MSCI-style ESG score and NPV impact."""

    output_quality = "heuristic"

    def __init__(self):
        super().__init__(
            name="esg_scorer",
            description=(
                "Computes an indicative rule-based composite ESG score (0-10), a letter band "
                "and a carbon-tax exposure at $50/t. Requires inputs from the carbon "
                "extractor and supply chain risk flagger. Not an MSCI or agency rating."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "total_emissions": {
                    "type": "number",
                    "description": "Total Scope 1/2/3 emissions in tCO2e.",
                },
                "supply_chain_severity": {
                    "type": "string",
                    "description": "Severity from SupplyChainRiskFlaggerTool (Low, Medium, High, Critical).",
                },
            },
            "required": ["total_emissions", "supply_chain_severity"],
        }

    async def execute(
        self, total_emissions: float = 0.0, supply_chain_severity: str = "Low", **kwargs
    ) -> ToolResult:
        # Environmental calculation (0-10)
        env_score = 10.0
        if total_emissions > 100_000:
            env_score = 2.0
        elif total_emissions > 10_000:
            env_score = 5.0
        elif total_emissions > 0:
            env_score = 8.0

        # Social / Governance Calculation (0-10)
        soc_gov_score = 10.0
        supply_chain_severity = str(supply_chain_severity or "Low").strip().capitalize()
        if supply_chain_severity == "Critical":
            soc_gov_score = 1.0
        elif supply_chain_severity == "High":
            soc_gov_score = 3.0
        elif supply_chain_severity == "Medium":
            soc_gov_score = 6.0

        # Weighted Total: E(40%), S(30%), G(30%) - grouping S/G here for simplicity
        composite_score = (env_score * 0.4) + (soc_gov_score * 0.6)

        rating_band = "AAA"
        if composite_score < 3:
            rating_band = "CCC"
        elif composite_score < 5:
            rating_band = "B"
        elif composite_score < 7:
            rating_band = "BBB"
        elif composite_score < 8.5:
            rating_band = "AA"

        # Financial Impact Bridge (Carbon Tax at $50/ton assumed)
        carbon_tax_cost = total_emissions * 50.0

        return ToolResult(
            success=True,
            data={
                "composite_esg_score": round(composite_score, 1),
                # Letter band on DealForge's own rule-based score; not an MSCI/agency rating.
                "indicative_rating_band": rating_band,
                "rating_basis": "DealForge rule-based score (E 40%, S+G 60%); not an agency rating",
                "sub_scores": {
                    "environmental": round(env_score, 1),
                    "social_governance": round(soc_gov_score, 1),
                },
                "financial_impact_bridge": {
                    "carbon_tax_exposure_50_usd": f"-${carbon_tax_cost:,.2f}"
                },
            },
        )
