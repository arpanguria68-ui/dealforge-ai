
import asyncio
import json
import os
import sys
from datetime import datetime

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.core.reports.report_generator import generate_pdf
from app.core.reports.branding import load_custom_branding
from app.core.reports.infographic_engine import InfographicEngine

async def test_sprint5_features():
    print("🚀 Starting Sprint 5 Verification...")
    
    # 1. Mock Deal State with Sprint 5 High-Fidelity Data
    mock_state = {
        "deal_id": "DEAL-ENT-999",
        "deal_name": "Project SkyNet Enterprise",
        "context": {
            "target_company": "SkyNet AI",
            "industry": "technology",
            "sector": "SaaS",
        },
        "fact_base": {
            "profile": {
                "name": "SkyNet AI",
                "sector": "Technology",
                "industry": "Artificial Intelligence",
            },
            "metrics": {
                "historical_revenue": [
                    {"year": "2021", "amount": 450},
                    {"year": "2022", "amount": 620},
                    {"year": "2023", "amount": 890},
                ],
                "financial_metrics": {
                    "ebitda": 220,
                    "revenue": 890,
                    "working_capital": 150,
                    "retained_earnings": 300,
                    "ebit": 250,
                    "market_value_equity": 5000,
                    "total_assets": 2000,
                    "total_liabilities": 500,
                    "sales": 890
                }
            }
        },
        "valuation_output": {
            "valuations": [
                {"method": "DCF (Base Case)", "low": 4200, "mid": 4850, "high": 5500},
                {"method": "EV/EBITDA Comps", "low": 3800, "mid": 4200, "high": 4900},
                {"method": "Precedent Trans.", "low": 4500, "mid": 5100, "high": 6000},
                {"method": "LBO (20% IRR)", "low": 3500, "mid": 4000, "high": 4500},
            ]
        },
        "final_score": 0.88,
        "consistency_warnings": [
            {
                "severity": "material",
                "message": "Revenue growth projections exceed sector median benchmarks.",
                "agents_involved": ["FinancialAnalyst", "MarketResearcher"]
            }
        ]
    }

    agent_results = [
        {
            "agent_type": "advanced_financial_modeler",
            "reasoning": "### Altman Z-Score: 6.42 (Safe)\nThe company exhibits strong liquidity and capitalization. Bankruptcy risk is minimal.\n\n### DuPont Analysis\nROE is driven primarily by strong operating margins (28%) rather than excessive leverage."
        },
        {
            "agent_type": "risk_assessor",
            "reasoning": "- **Market Saturation [HIGH]**: Competition in AI chips is intensifying.\n  - *Evidence*: Per SEC 10-K, 3 major competitors launched rival chips in Q4.\n- **Data Privacy [MEDIUM]**: Regulatory headwinds in EU.\n  - *Evidence*: FactBase notes 15% revenue from GDPR-sensitive regions."
        }
    ]

    analyst_data = {
        "executive_summary": {
            "situation": "SkyNet AI is a leader in Enterprise AI software.",
            "complication": "High churn in mid-market segment.",
            "question": "Should we proceed with the $4.8B acquisition?",
            "answer": "PROCEED. Strong core metrics and safe Z-score outweigh churn risks."
        }
    }

    # 2. Test PDF Generation with Embedded Charts
    print("📊 Generating Enterprise PDF with Football Field & Waterfall...")
    
    pdf_path = f"f:/code project/Kimi_Agent_DealForge AI PRD/reports/enterprise_sprint5_test_{datetime.now().strftime('%H%M%S')}.pdf"
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    
    # PDF gen - Correct signature: (deal, analyst_data, agent_results, provenance_records=None, deal_stage="deep_dive")
    with open(pdf_path, "wb") as f:
        pdf_bytes = generate_pdf(
            deal=mock_state,
            analyst_data=analyst_data,
            agent_results=agent_results,
            provenance_records=mock_state.get("consistency_warnings")
        )
        f.write(pdf_bytes)
    
    print(f"✅ PDF Generated: {pdf_path}")
    
    # 3. Verify Sector Prompts (Dry Run)
    print("🔍 Verifying Sector Loader Benchmarks...")
    from app.core.sector_loader import load_sector_config, build_sector_prompt
    tech_config = load_sector_config("tech")
    prompt = build_sector_prompt("financial_analyst", tech_config)
    
    if "Sector Benchmarks" in prompt:
        print("✅ Sector Benchmarks correctly injected into prompt.")
    else:
        print("❌ Sector Benchmarks MISSING from prompt.")

    print("🏁 Verification Complete!")

if __name__ == "__main__":
    asyncio.run(test_sprint5_features())
