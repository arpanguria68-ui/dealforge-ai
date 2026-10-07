"""Stakeholder Reaction Simulation for DealForge AI (F-028)."""
import json
from typing import Dict, Any, List
import structlog
from app.core.llm.llm_gateway import get_llm_gateway

logger = structlog.get_logger(__name__)

class StakeholderSimulation:
    """Simulates perspectives from high-level corporate stakeholders (F-028)."""

    STAKEHOLDERS = {
        "cfo": {
            "name": "Chief Financial Officer",
            "priorities": ["Capital allocation efficiency", "EPS accretion", "Leverage ratios", "Cash flow stability", "Tax implications"],
            "persona": "Conservative, number-driven, focused on long-term shareholder value and balance sheet health."
        },
        "cto": {
            "name": "Chief Technology Officer",
            "priorities": ["Tech stack integration", "Technical debt", "Scalability", "Cybersecurity", "IP quality", "Talent retention"],
            "persona": "Pragmatic, risk-aware, focused on product longevity, security, and the 'built not bought' trade-off."
        },
        "general_counsel": {
            "name": "General Counsel",
            "priorities": ["Regulatory hurdles", "Litigation history", "Compliance", "Contractual anti-trust issues", "IP protection"],
            "persona": "Risk-averse, meticulous, focused on minimizing legal exposure and ensuring regulatory approval."
        },
        "board": {
            "name": "Executive Board",
            "priorities": ["Strategic alignment", "Market perception", "ESG impact", "Dividend impact", "Brand reputation"],
            "persona": "Strategic, visionary but cautious, focused on the 'Big Picture' and long-term market positioning."
        }
    }

    def __init__(self, provider: str = "gemini", model: str = None):
        self.gateway = get_llm_gateway()
        self.provider = provider
        self.model = model

    async def run_simulation(self, deal_name: str, deal_industry: str, findings_summary: str) -> Dict[str, Any]:
        """Run the simulation across all defined stakeholders."""
        results = {}
        
        for key, config in self.STAKEHOLDERS.items():
            prompt = f"""
            Analyze this deal from the perspective of the {config['name']}.
            
            Persona: {config['persona']}
            Priorities: {', '.join(config['priorities'])}
            
            Deal: {deal_name}
            Industry: {deal_industry}
            Findings Summary:
            {findings_summary[:3000]}
            
            Return a JSON object with:
            - sentiment_score: 1-10 (1=Strongly Oppose, 10=Strongly Support)
            - top_concerns: List of 3 strings
            - top_value_drivers: List of 3 strings
            - approval_conditions: Best guess on what would make you approve this deal.
            - summary: 1-2 sentence justification.
            """
            
            try:
                response = await self.gateway.call(
                    provider=self.provider,
                    model=self.model,
                    prompt=prompt,
                    system_prompt=f"You are the {config['name']} of a Tier-1 Private Equity firm or Fortune 500 company.",
                    json_mode=True
                )
                
                content = response.get("content", {})
                if isinstance(content, str):
                    content = json.loads(content)
                    
                results[key] = content
            except Exception as e:
                logger.error(f"simulation_failed_for_{key}", error=str(e))
                results[key] = {"error": "Simulation failed"}

        # Calculate consensus score
        sentiment_scores = [r.get("sentiment_score", 5) for r in results.values() if "sentiment_score" in r]
        avg_score = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 5.0
        
        return {
            "reactions": results,
            "consensus_sentiment": round(avg_score, 1),
            "alignment_summary": "High alignment" if avg_score >= 7.5 else "Mixed sentiment" if avg_score >= 5.0 else "Low alignment/Conflict"
        }
