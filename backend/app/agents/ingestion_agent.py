"""
Ingestion Agent — The "First Node" agent that extract the structured FactBase.
"""

from typing import Dict, Any, Optional, List
import json
from datetime import datetime

from app.agents.base import BaseAgent, AgentOutput
from app.core.models.fact_base import DealFactBase, CompanyProfile, FinancialMetrics, DealTerms
from app.core.json_helpers import extract_and_parse_json

class IngestionAgent(BaseAgent):
    """
    IngestionAgent analyzes the deal brief and uploaded documents to populate 
    the DealFactBase. It identifies missing critical data and sets the ground 
    truth for downstream analysis.
    """

    name = "ingestion_agent"
    description: str = "Analyzes deal inputs to create a structured FactBase (Ground Truth)."
    recommended_model: str = "Gemini 1.5 Pro (Deep Extraction)"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        start_time = datetime.now()
        self.logger.info("Starting IngestionAgent execution", task=task)

        context = context or {}
        deal_id = context.get("deal_id", "unknown")
        
        # 1. Retrieve initial context from documents (RAG)
        doc_context = []
        if deal_id != "unknown":
            doc_context = await self.memory.query(
                f"financial statements, company overview, revenue, ebitda, deal terms for {context.get('company_name', 'target company')}",
                deal_id=deal_id,
                top_k=10
            )

        # 2. Build Prompt for extraction
        prompt = self._build_extraction_prompt(task, context, doc_context)
        system_prompt = self._build_system_prompt()

        # 3. Call LLM for extraction
        gateway = get_llm_gateway()
        response = await gateway.call(
            provider="gemini",
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.1  # High precision
        )

        content = response.get("content", "")
        extracted_data = self._parse_output(content)

        # 4. Initialize and populate DealFactBase
        try:
            # Cleanup industrial strings to float
            fact_base = self._create_fact_base(extracted_data)
            
            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            return AgentOutput(
                success=True,
                data=fact_base.dict(),
                reasoning="Extracted structured FactBase from available deal documents and context.",
                confidence=0.85,
                execution_time_ms=execution_time,
                tool_calls=response.get("function_calls"),
            )

        except Exception as e:
            self.logger.error("FactBase creation failed", error=str(e))
            return AgentOutput(
                success=False,
                data={},
                reasoning=f"Ingestion failed during FactBase mapping: {str(e)}",
                confidence=0.0,
            )

    def _build_system_prompt(self) -> str:
        return """You are the Ingestion Agent for DealForge AI.
Your ONLY goal is to extract specific, quantitative, and structural facts about a deal to populate the DealFactBase.

REQUIRED DATA FIELDS:
1. Company Profile: Name, Ticker, Industry, Sector, Employees.
2. Financials: 
   - Historical Revenue/EBITDA/Net Income (by year).
   - Projected Revenue/EBITDA (Year 1-5).
   - Key margins and growth rates.
3. Deal Terms: Asking price, structure, enterprise value.

RULES:
- Be extremely precise with numbers.
- If data is missing for a field, leave it as null/empty but list it in "missing_critical_data".
- Link every major fact to a source (e.g., "CIM Page 4" or "Chat History").
- Output MUST be a valid JSON matching the DealFactBase structure.
"""

    def _build_extraction_prompt(self, task: str, context: Dict, doc_context: List) -> str:
        prompt = f"TASK: {task}\n\n"
        prompt += "CONTEXT DATA:\n"
        prompt += json.dumps(context, default=str, indent=2) + "\n\n"
        
        if doc_context:
            prompt += "DOCUMENT EXCERPTS (RAG):\n"
            for i, chunk in enumerate(doc_context):
                prompt += f"--- Source {i+1} ({chunk.get('metadata', {}).get('filename', 'Unknown')}) ---\n"
                prompt += chunk.get("content", "") + "\n"
        
        prompt += "\nExtract all available financial and company facts into the DealFactBase JSON format."
        return prompt

    def _parse_output(self, content: str) -> Dict:
        return extract_and_parse_json(content) or {}

    def _create_fact_base(self, data: Dict) -> DealFactBase:
        # Map raw data to pydantic models
        profile = CompanyProfile(**data.get("profile", {"name": "Unknown", "industry": "General", "sector": "General"}))
        metrics = FinancialMetrics(**data.get("metrics", {}))
        terms = DealTerms(**data.get("terms", {}))
        
        return DealFactBase(
            profile=profile,
            metrics=metrics,
            terms=terms,
            source_map=data.get("source_map", {}),
            missing_critical_data=data.get("missing_critical_data", [])
        )

# Helper for singleton-style access if needed
from app.core.llm.llm_gateway import get_llm_gateway
