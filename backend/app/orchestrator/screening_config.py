"""Deal-specific task maps for the screening phase (F-031)."""
from typing import Dict, Any

class ScreeningTaskMap:
    """Pre-built task templates for each industry to refine initial screening."""

    TEMPLATES = {
        "saas": {
            "financial_analyst": "Quickly assess: (1) ltm_revenue_growth, (2) gross_margin, (3) cac_payback, (4) net_retention. Use benchmarks for B2B SaaS.",
            "market_researcher": "Verify TAM/SAM, competitive landscape (G2/Gartner view), and product-market fit signals.",
            "risk_assessor": "Identify platform dependencies, high churn cohorts, and pricing power risks.",
            "legal_advisor": "Scan for IP ownership issues and standard SaaS subscription terms."
        },
        "manufacturing": {
            "financial_analyst": "Focus on EBITDA stability, Capex requirements, and working capital cycles (Inventory turns).",
            "market_researcher": "Assess global demand trends, raw material price sensitivity, and factory utilization benchmarks.",
            "risk_assessor": "Check supply chain concentration and environmental/safety compliance history.",
            "legal_advisor": "Verify long-term supply contracts and labor union constraints."
        },
        "biotech": {
            "financial_analyst": "Evaluate R&D burn rate, remaining runway, and NPV based on Phase II/III success probabilities.",
            "market_researcher": "Check epidemiology data, competitive pipeline (ClinicalTrials.gov), and standard of care shifts.",
            "risk_assessor": "Assess regulatory hurdle risks (FDA/EMA) and clinical trial failure probabilities.",
            "legal_advisor": "Focus on patent runway, FTO (Freedom to Operate), and licensing agreements."
        },
        "default": {
            "financial_analyst": "Standard P&L and Balance Sheet assessment.",
            "market_researcher": "General market size and competitive landscape.",
            "risk_assessor": "Top 10 business and financial risks.",
            "legal_advisor": "General legal and regulatory compliance check."
        }
    }

    @classmethod
    def get_tasks(cls, industry: str) -> Dict[str, str]:
        """Return the best-match template for the industry."""
        industry_lower = industry.lower()
        
        for key in cls.TEMPLATES:
            if key in industry_lower:
                return cls.TEMPLATES[key]
                
        return cls.TEMPLATES["default"]
