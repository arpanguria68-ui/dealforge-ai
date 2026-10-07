"""Dynamic Agent Selection Planner for DealForge AI (F-026)."""
from typing import List, Dict, Any, Optional
import structlog
from app.orchestrator.state import DealState

logger = structlog.get_logger(__name__)

class AgentSelectionPlanner:
    """Determines which agents to run based on deal context (Industry, Size, Stage)."""

    def __init__(self):
        self.logger = logger

    async def plan_execution(self, state: DealState) -> List[str]:
        """
        Return list of agent names to execute.
        
        Logic:
        1. Base experts (Financial, Legal, Risk, Market) are always included for deep-dive.
        2. Specialized agents added based on Industry (Tech, Biotech, Manufacturing).
        3. 'advanced_financial' added if deal size > $500M.
        """
        context = state.get("context", {})
        industry = context.get("industry", "").lower()
        deal_size = state.get("deal_size", 0) or 0

        from app.agents.base import get_agent_registry

        registry = get_agent_registry()
        registered = set(registry.list_agents())
        base_agents = [
            name for name in (
                "financial_analyst", "legal_advisor", "risk_assessor", "market_researcher"
            ) if name in registered
        ]
        selected_agents = list(base_agents)

        # Laya can add specialist coverage, but it must never remove the core
        # diligence panel. Model-reported confidence is not calibrated enough
        # to decide that financial/legal/risk/market work is unnecessary.
        brief = context.get("deal_brief", "") or context.get("user_prompt", "")
        if brief:
            try:
                from app.core.laya.client import get_laya_client

                triage = await get_laya_client().triage_deal(brief)
                threshold = 0.85
                specialist = {
                    "financial": "financial_analyst",
                    "legal": "legal_advisor",
                    "risk": "risk_assessor",
                    "market": "market_researcher",
                    "tech": "ai_tech_diligence_agent",
                    "esg": "esg_agent",
                }.get(triage.get("track")) if triage else None
                if (
                    triage
                    and triage.get("backend") in {"local", "remote"}
                    and triage.get("needs_deep_dive") is True
                    and triage.get("track_confidence", 0) >= threshold
                    and specialist in registered
                    and specialist not in selected_agents
                ):
                    selected_agents.append(specialist)
            except Exception as exc:
                self.logger.warning("laya_agent_triage_failed", error=str(exc))

        # Add registered specialists only when the deal context justifies them.
        specialist = None
        if any(w in industry for w in ["tech", "software", "saas"]):
            specialist = "ai_tech_diligence_agent"
        elif any(w in industry for w in ["biotech", "pharma", "clinical", "healthcare"]):
            specialist = "clinical_trial_analyzer"
        elif any(w in industry for w in ["manufactur", "industrial", "automotive"]):
            specialist = "supply_chain_risk_agent"
        if specialist in registered and specialist not in selected_agents:
            selected_agents.append(specialist)

        if deal_size > 500_000_000:
            for candidate in ("advanced_financial_modeler", "advanced_financial"):
                if candidate in registered and candidate not in selected_agents:
                    selected_agents.append(candidate)
                    break

        selected_agents = list(dict.fromkeys(selected_agents))
        self.logger.info("agent_selection_planned", 
                         industry=industry, 
                         deal_size=deal_size, 
                         selected=selected_agents)
        
        return selected_agents
