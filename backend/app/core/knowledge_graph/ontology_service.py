"""Service for generating industry-specific graph ontologies (F-022)."""
import json
from typing import Any, Dict, List, Optional
import structlog
from app.core.llm.llm_gateway import get_llm_gateway

logger = structlog.get_logger(__name__)

class OntologyService:
    """Auto-generates entity types and relationships based on deal context (F-022)."""

    def __init__(self, provider: str = "gemini", model: Optional[str] = None):
        self.provider = provider
        self.model = model
        self.gateway = get_llm_gateway()

    async def generate_ontology(self, industry: str, deal_brief: str) -> Dict[str, Any]:
        """
        Use LLM to generate industry-specific ontology nodes and relationships.
        """
        prompt = f"""
        Industry: {industry}
        Deal Brief snippet: {deal_brief[:1000]}

        Design a custom knowledge graph schema for this specific deal. 
        Identify the most important entity types (beyond generic Company/Risk) and how they relate.

        Return pure JSON with keys:
        - entities: list of strings (e.g., ["Product", "IP_Patent", "Clinical_Trial"])
        - relationships: list of objects with {{"from": "...", "to": "...", "label": "..."}}
        - properties: dict mapping entity type to list of important attributes

        Ensure labels are CamelCase and concise.
        """
        
        try:
            response = await self.gateway.call(
                provider=self.provider,
                model=self.model,
                prompt=prompt,
                system_prompt="You are an expert Ontologist and Knowledge Graph architect.",
                json_mode=True
            )
            
            content = response.get("content", {})
            if isinstance(content, str):
                content = json.loads(content)
                
            logger.info("ontology_generated", industry=industry, entities=content.get("entities", []))
            return content
        except Exception as e:
            logger.error("ontology_generation_failed", error=str(e))
            return {
                "entities": ["Company", "Risk", "Person", "Metric"],
                "relationships": [
                    {"from": "Deal", "to": "Company", "label": "INVOLVES"},
                    {"from": "Deal", "to": "Risk", "label": "HAS_RISK"}
                ],
                "properties": {}
            }
