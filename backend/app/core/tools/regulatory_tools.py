"""
Regulatory & Cyber Tools for DealForge AI.

Cybersecurity and GDPR/privacy findings extracted from supplied text (quote-verified,
see grounded_extraction.py) and HHI antitrust calculations.
"""

from typing import Dict, Any, List, Optional
import structlog
from app.core.tools.tool_router import BaseTool, ToolResult
from app.core.tools.grounded_extraction import (
    EXTRACTED_NOTE, HEURISTIC_NOTE, SEVERITY_RANK, extract_grounded, max_severity, pick,
)

logger = structlog.get_logger(__name__)


class CyberVulnScannerTool(BaseTool):
    """Extracts quote-backed cybersecurity findings; rule-based cost and risk rating."""

    output_quality = "extracted"

    # Indicative remediation budget per finding category (USD, rule-based).
    REMEDIATION_COST = {
        "breach": 500_000, "ransomware": 1_000_000, "missing_certification": 100_000,
        "unpatched_vulnerability": 150_000, "access_control": 75_000,
        "third_party": 100_000, "other": 50_000,
    }

    def __init__(self):
        super().__init__(
            name="cyber_vuln_scanner",
            description=(
                "Reads security policies or technical text and extracts cybersecurity "
                "findings (past breaches, ransomware exposure, missing SOC2/ISO 27001, "
                "unpatched systems, access-control gaps), each backed by a verbatim quote, "
                "with a rule-based remediation estimate and risk rating."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "security_text": {
                    "type": "string",
                    "description": "Text describing the target company's cybersecurity posture or history.",
                },
            },
            "required": ["security_text"],
        }

    async def execute(self, security_text: str = "", **kwargs) -> ToolResult:
        items = await extract_grounded(
            "cyber_vuln_scanner",
            security_text,
            "Identify current cybersecurity weaknesses or incidents affecting the company.",
            {
                "category": "one of breach|ransomware|missing_certification|unpatched_vulnerability|access_control|third_party|other",
                "finding": "one-sentence description",
                "severity": "one of low|medium|high|critical",
            },
        )
        if items is None:
            findings = self._keyword_findings(security_text)
            quality, note = "heuristic", HEURISTIC_NOTE
        else:
            findings = [
                {
                    "category": pick(i.get("category"), tuple(self.REMEDIATION_COST), "other"),
                    "finding": str(i.get("finding") or "")[:300],
                    "severity": pick(i.get("severity"), tuple(SEVERITY_RANK), "medium"),
                    "quote": i["quote"],
                }
                for i in items
            ]
            quality, note = "extracted", EXTRACTED_NOTE

        worst = max_severity([f["severity"] for f in findings], default="")
        rating = (
            "High Risk" if SEVERITY_RANK.get(worst, 0) >= 3 or len(findings) > 1
            else "Medium Risk" if findings else "Low Risk"
        )
        return ToolResult(
            success=True,
            data={
                "vulnerabilities_detected": [f["finding"] for f in findings],
                "vulnerabilities_count": len(findings),
                "findings": findings,
                "remediation_cost_estimate_usd": sum(
                    self.REMEDIATION_COST.get(f["category"], 50_000) for f in findings
                ),
                "compliance_flags": rating,
                "data_quality": quality,
                "method_note": note,
            },
        )

    @staticmethod
    def _keyword_findings(security_text: str) -> List[Dict[str, Any]]:
        text = (security_text or "").lower()
        findings = []
        if "breach" in text or "hacked" in text or "compromised" in text:
            findings.append({"category": "breach", "severity": "high", "quote": None,
                             "finding": "Historical data breach or compromise mentioned."})
        if "ransomware" in text:
            findings.append({"category": "ransomware", "severity": "critical", "quote": None,
                             "finding": "Ransomware exposure mentioned."})
        if "no soc2" in text or "lack of soc2" in text or "not soc2 compliant" in text:
            findings.append({"category": "missing_certification", "severity": "medium", "quote": None,
                             "finding": "Missing SOC2 Type II compliance."})
        return findings


class AntitrustHHICalculatorTool(BaseTool):
    """Computes Herfindahl-Hirschman Index (HHI) for antitrust risk."""

    def __init__(self):
        super().__init__(
            name="antitrust_hhi_calculator",
            description=(
                "Computes the Herfindahl-Hirschman Index (HHI) to estimate antitrust "
                "risk. Requires an array of decimal market shares (e.g., [0.40, 0.20])."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "market_shares": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "List of top firm market shares as decimals, e.g., [0.40, 0.20, 0.15].",
                },
            },
            "required": ["market_shares"],
        }

    async def execute(self, market_shares: List[float] = None, **kwargs) -> ToolResult:
        if not market_shares:
            return ToolResult(
                success=False, data=None, error="No market shares provided"
            )

        # HHI = Sum of squared market shares (where share is a whole number %, but input is decimal)
        # So we multiply decimal by 100, square it. Equivalently: decimal^2 * 10000.
        try:
            hhi = sum((share * 100) ** 2 for share in market_shares)
        except Exception as e:
            return ToolResult(
                success=False, data=None, error=f"Invalid share data: {str(e)}"
            )

        classification = "Low"
        probability = "Highly Probable (Safe Harbor)"

        if hhi > 2500:
            classification = "High Concentration"
            probability = "Low (Highly Scrutinized by FTC/DOJ)"
        elif hhi > 1500:
            classification = "Moderate Concentration"
            probability = "Moderate (May require concessions)"

        return ToolResult(
            success=True,
            data={
                "calculated_hhi": round(hhi, 2),
                "risk_classification": classification,
                "clearance_probability": probability,
            },
        )


class PrivacyAuditorTool(BaseTool):
    """Extracts quote-backed privacy/data-transfer issues and safeguards."""

    output_quality = "extracted"

    def __init__(self):
        super().__init__(
            name="privacy_auditor",
            description=(
                "Reads data-flow and privacy text and extracts GDPR / Schrems II exposures "
                "(cross-border transfers, missing DPO, consent, retention) and the safeguards "
                "in place (SCCs, Data Privacy Framework, BCRs), each backed by a verbatim quote."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "data_flow_text": {
                    "type": "string",
                    "description": "Text detailing the target's data storage locations and EU/US data flows.",
                },
            },
            "required": ["data_flow_text"],
        }

    async def execute(self, data_flow_text: str = "", **kwargs) -> ToolResult:
        items = await extract_grounded(
            "privacy_auditor",
            data_flow_text,
            "Identify privacy compliance issues (kind=issue) and privacy safeguards in place "
            "(kind=safeguard) described in the text.",
            {
                "kind": "issue|safeguard",
                "topic": "one of cross_border_transfer|dpo|consent|retention|breach_notification|other",
                "finding": "one-sentence description",
                "severity": "for issues: low|medium|high|critical",
            },
        )
        if items is None:
            issues, safeguards = self._keyword_issues(data_flow_text), []
            quality, note = "heuristic", HEURISTIC_NOTE
        else:
            issues, safeguards = [], []
            for i in items:
                entry = {
                    "topic": pick(i.get("topic"), ("cross_border_transfer", "dpo", "consent", "retention",
                                                   "breach_notification", "other"), "other"),
                    "finding": str(i.get("finding") or "")[:300],
                    "quote": i["quote"],
                }
                if pick(i.get("kind"), ("issue", "safeguard"), "issue") == "safeguard":
                    safeguards.append(entry)
                else:
                    issues.append({**entry, "severity": pick(i.get("severity"), tuple(SEVERITY_RANK), "medium")})
            quality, note = "extracted", EXTRACTED_NOTE

        material = [i for i in issues if SEVERITY_RANK.get(i.get("severity"), 2) >= 2]
        return ToolResult(
            success=True,
            data={
                "privacy_audit_findings": [i["finding"] for i in issues]
                or ["No privacy framework violations identified in the provided text."],
                "issues": issues,
                "safeguards": safeguards,
                "audit_status": "Failed" if material else "Passed",
                "data_quality": quality,
                "method_note": note,
            },
        )

    @staticmethod
    def _keyword_issues(data_flow_text: str) -> List[Dict[str, Any]]:
        text = (data_flow_text or "").lower()
        issues = []
        is_eu = "eu " in text or "europe" in text or "european" in text or "gdpr" in text
        is_us = "us " in text or "united states" in text or "america " in text
        if is_eu and is_us and ("store" in text or "transfer" in text):
            if "scc" not in text and "standard contractual clauses" not in text:
                issues.append({
                    "topic": "cross_border_transfer", "severity": "high", "quote": None,
                    "finding": "Potential Schrems II exposure: EU data transferred to US without "
                               "explicit SCCs or Data Privacy Framework mention.",
                })
        if "gdpr" in text and "no dpo" in text:
            issues.append({"topic": "dpo", "severity": "medium", "quote": None,
                           "finding": "Missing Data Protection Officer (DPO) despite GDPR exposure."})
        return issues
