import asyncio
import structlog
from app.orchestrator.graph import get_orchestrator
from app.orchestrator.state import create_initial_state, DealStage
from app.config import get_settings
import json

# Configure logging
structlog.configure()
logger = structlog.get_logger()

async def run_e2e_test():
    """Run a full deal flow using Vertex AI (Phase 5 E2E Test)"""
    settings = get_settings()
    logger.info("Starting E2E Test", 
                provider=settings.DEFAULT_LLM_PROVIDER, 
                model=settings.VERTEX_MODEL)
    
    # Check if Vertex is configured
    if not settings.VERTEX_API_KEY:
        logger.error("VERTEX_API_KEY_MISSING")
        return

    orchestrator = get_orchestrator()
    
    deal_id = "test-vertex-e2e-001"
    deal_name = "Project Skybound - SaaS Expansion"
    
    # Context with industry and deal brief
    context = {
        "industry": "SaaS",
        "deal_size_usd": 50000000,
        "target_company": "Skybound Systems",
        "deal_brief": """
        Skybound Systems is a mid-market SaaS provider of CRM solutions for the renewable energy sector.
        - Revenue: $12M ARR (35% YOY growth)
        - EBITDA: $1.5M (12.5% margin)
        - Customer Base: 450 enterprise clients
        - Key Risk: High concentration in European markets.
        - Thesis: Strategic bolt-on for global CRM player to enter green-tech vertical.
        """,
        "deal_stage": "deep_dive"
    }
    
    logger.info("Running orchestrator.run_deal...")
    
    try:
        final_state = await orchestrator.run_deal(
            deal_id=deal_id,
            deal_name=deal_name,
            context=context
        )
        
        logger.info("Workflow completed!", 
                    stage=final_state.get("current_stage"),
                    recommendation=final_state.get("final_recommendation"))
        
        # Verify Phase 5 features
        logger.info("Verifying Phase 5 Outputs...")
        
        if final_state.get("selected_agents"):
            logger.info("F-026 (Planner) SUCCEEDED", agents=final_state["selected_agents"])
        else:
            logger.warning("F-026 (Planner) FAILED or No agents selected")
            
        if final_state.get("stakeholder_reactions"):
            logger.info("F-028 (Simulation) SUCCEEDED", 
                        consensus=final_state["stakeholder_reactions"].get("consensus_sentiment"))
        else:
            logger.warning("F-028 (Simulation) FAILED")
            
        if final_state.get("quality_results"):
             logger.info("F-027 (Quality Gates) SUCCEEDED", gates=list(final_state["quality_results"].keys()))
        else:
             logger.warning("F-027 (Quality Gates) FAILED")

        # Save results to a file for review
        with open("e2e_test_results.json", "w") as f:
            # Clean state for JSON serialization
            serializable = {k: v for k, v in final_state.items() if not k.startswith("_")}
            json.dump(serializable, f, indent=2, default=str)
            
    except Exception as e:
        logger.error("E2E Test Failed", error=str(e))
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(run_e2e_test())
