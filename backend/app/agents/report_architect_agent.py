"""
Report Architect Agent
"""

from typing import Dict, Any, Optional, List
import json
from datetime import datetime

from app.agents.base import BaseAgent, AgentOutput
from app.core.json_helpers import extract_and_parse_json


class ReportArchitectAgent(BaseAgent):
    """
    Selects and configures report templates based on deal type, audience,
    and customization preferences.
    """

    name = "report_architect"
    description: str = (
        "Configures report templates, visual component manifest, and layout rules."
    )
    recommended_model: str = "Gemini 1.5 Pro (High-Fidelity Synthesis)"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        start_time = datetime.now()
        self.logger.info("Starting ReportArchitectAgent execution", task=task)

        context = context or {}
        deal_id = context.get("deal_id", "unknown")

        prompt = self._build_prompt(task, context, [])
        system_prompt = self._build_system_prompt()

        # Document planning is a bounded formatting task; tools and retrieved facts
        # would add cost and create an unnecessary path for unsupported claims.
        try:
            response = await self.generate_with_routed_fallback(prompt, system_prompt)
        except Exception as exc:
            self.logger.error("report_architect_provider_unavailable", error=str(exc))
            return AgentOutput(
                success=False,
                data={},
                reasoning=f"Report architecture provider unavailable: {type(exc).__name__}.",
                confidence=0.0,
            )

        try:
            content = response.get("content", "") if isinstance(response, dict) else str(response)
            blueprint = self._parse_output(content)
            if not isinstance(blueprint, dict) or "sections" not in blueprint:
                raise ValueError("Architect response did not contain a valid blueprint.")

            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            return AgentOutput(
                success=True,
                data=blueprint,
                reasoning="Generated report configuration blueprint.",
                confidence=0.9,
                execution_time_ms=execution_time,
                tool_calls=[],
            )

        except Exception as e:
            self.logger.error("Report architecture failed", error=str(e))
            return AgentOutput(
                success=False,
                data={},
                reasoning=f"Report architecture failed: {str(e)}",
                confidence=0.0,
            )

    def _build_system_prompt(self) -> str:
        return """You are a document-planning agent for investment and consulting deliverables.
Choose a concise section order and visuals only from the supplied allow-lists and only when the evidence inventory says the data exists. The input inventory is a capability map, not evidence to embellish. Do not create financial claims, recommendations, mitigations, charts, or sections that imply unavailable analysis.
Return ONLY JSON with this schema:
{"sections":["executive_summary"],"visuals":[],"omitted_sections":[],"density":"standard","page_orientation":"portrait","planning_rationale":"One or two concise sentences explaining audience/coverage choices; no hidden chain-of-thought."}
Use only section IDs provided in allowed_sections and visual IDs provided in available_visuals. density must be compact, standard, or detailed. page_orientation must be portrait or landscape. Keep rationale under 500 characters."""

    def _build_prompt(self, task: str, context: Dict, memory: List) -> str:
        company_name = context.get("company_name", "the target company")
        prompt = f"TASK: {task}\n"
        prompt += f"TARGET COMPANY: {company_name}\n"
        prompt += "DOCUMENT CONSTRAINTS (JSON):\n"
        prompt += json.dumps({
            "industry": context.get("industry", "General"),
            "audience": context.get("audience", "Investment Committee"),
            "available_data": context.get("available_data", {}),
            "evidence_brief": context.get("evidence_brief", {}),
            "allowed_sections": context.get("allowed_sections", []),
            "available_visuals": context.get("available_visuals", []),
        }, ensure_ascii=True, default=str)

        if memory:
            prompt += "PREVIOUS REPORT ARCHITECTURES ALIGNED TO THIS CONTEXT:\n"
            for m in memory:
                content = m.get("content", "") if isinstance(m, dict) else str(m)
                prompt += f"- {content[:300]}...\n"
            prompt += "\n"

        prompt += f"Create the report blueprint for {company_name}. "
        prompt += "CRITICAL: Based ONLY on the available context. If data is sparse, adapt the blueprint accordingly without making up company facts."
        return prompt

    def _parse_output(self, content: str) -> Dict:
        parsed = extract_and_parse_json(content)
        if parsed:
            return parsed
        raise ValueError("Report blueprint response was not valid JSON.")
