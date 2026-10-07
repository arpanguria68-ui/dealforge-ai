
import asyncio
import os
import sys

# Set up paths
sys.path.append(os.getcwd())

from app.core.models.fact_base import DealFactBase
from app.core.reports.report_generator import generate_pdf
from app.core.reports.branding import MBB_THEME

async def test_pdf_generation():
    print("Testing PDF Generation with MBB Branding...")
    
    deal = {
        "name": "Project Apollo - Series B Analysis",
        "target_company": "Apollo Tech Systems",
        "industry": "Clean Energy",
        "final_score": 0.85,
        "branding_id": "mbb",
        "fact_base": {
            "profile": {"name": "Apollo Tech Systems", "sector": "SaaS"},
            "metrics": {
                "revenue": 50.0,
                "ebitda": 12.0,
                "revenue_growth": 0.45
            }
        }
    }
    
    analyst_data = {
        "executive_summary": {
            "situation": "Apollo is a leading clean energy tech provider.",
            "complication": "High burn rate due to rapid R&D expansion.",
            "question": "Is the market growth sufficient to offset the capital requirements?",
            "answer": "Yes, with a 45% CAGR, the ROI is projected at 3.5x over 5 years."
        }
    }
    
    agent_results = [
        {
            "agent_type": "financial_analyst",
            "reasoning": "# Robust Financials\nApollo shows strong unit economics.\n- LTV/CAC: 4.2x\n- Payback: 6 months",
            "confidence": 0.9
        },
        {
            "agent_type": "risk_assessor",
            "reasoning": "Main risks are regulatory shifts in clean energy subsidies.",
            "confidence": 0.75
        }
    ]
    
    pdf_bytes = generate_pdf(deal, analyst_data, agent_results)
    
    output_path = "test_apollo_report.pdf"
    with open(output_path, "wb") as f:
        f.write(pdf_bytes)
    
    print(f"PDF generated successfully at {output_path}")
    print(f"File size: {len(pdf_bytes)} bytes")

if __name__ == "__main__":
    asyncio.run(test_pdf_generation())
