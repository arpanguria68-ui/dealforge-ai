import json
from typing import Dict, Any, List
from app.agents.base import BaseAgent, AgentOutput


class BusinessAnalystAgent(BaseAgent):
    """
    McKinsey Business Analyst Agent
    Specializes in synthesizing raw agent outputs into highly-structured,
    consulting-grade presentations (SCQA frameworks, MECE analysis).
    """

    name = "business_analyst"
    description: str = "Synthesizes raw deal analysis into McKinsey-style executive reports with SCQA and MECE formatting."
    recommended_model: str = "Gemini 1.5 Pro (Unit Economics)"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.system_prompt = """
        You are a top-tier McKinsey Engagement Manager specializing in M&A Due Diligence.
        Your task is to take the raw, unstructured findings from various specialized agents (Financial, Legal, Risk, Market) and synthesize them into a highly-structured, executive-level presentation payload.

        Write like a senior transaction-services partner: lead with the decision-relevant conclusion,
        quantify only what the supplied evidence supports, explain the driver and its implication,
        and end with the specific diligence question that would change the view. Keep prose crisp.
        Use SCQA only when the recorded evidence supports each section.
        Do not invent or extrapolate facts, figures, forecasts, peer data, valuation, mitigations,
        confidence, sources, or recommendations. Preserve contradictions and uncertainty explicitly.
        A missing value must be null or described as not established, never estimated from a default.
        Distinguish reported facts, derived calculations, agent judgments, user-supplied assumptions,
        and open diligence questions. Cite source IDs such as [S1] immediately after sourced claims.
        Never state that a source was independently checked. Do not convert data gaps into risks or
        opportunities unless an agent explicitly recorded that interpretation.
        Never turn a score threshold into a buy/sell/proceed recommendation.

        OUTPUT INSTRUCTIONS:
        You MUST return ONLY a valid JSON object with the exact structure below. Do NOT output any markdown blocks (```json), just the raw JSON.

        {
            "executive_summary": {
                    "situation": "One-sentence context with the company, period, and evidence coverage; cite source IDs.",
                "complication": "Most material evidence-backed issue or explicitly state that material issues were not established.",
                "question": "Decision question implied by the supplied mandate; do not invent buyer intent.",
                "answer": "Recorded recommendation only; otherwise state no recommendation recorded. Include the binding caveat."
            },
            "key_takeaways": [
                {
                    "title": "Short evidence-backed takeaway",
                    "description": "Claim, quantified evidence and period, decision implication, and limitation; cite [S#] and source agent."
                },
                ... (0-4 evidence-supported takeaways; fewer is better than padding)
            ],
            "financial_synthesis": {
                    "narrative": "Two to four sentences: trend, latest-period context, implication only if supported, then limitation. Cite [S#]; no generic filler.",
                "key_metrics": {
                    "metric_name": "source-reported value or null when not present"
                }
            },
            "risk_matrix": [
                {
                    "category": "Recorded category or unknown",
                    "severity": "Recorded severity or unknown",
                    "mitigation_strategy": "Recorded mitigation or not provided"
                },
                ...
            ],
            "action_items": [
                "Follow-up question or action directly supported by an identified data gap."
            ]
        }
        """

    async def run(self, task: str, context=None) -> AgentOutput:
        """Execute agent task (required by BaseAgent abstract method)"""
        import time

        start = time.time()
        result = await self._execute_task(task, context or {})
        result.execution_time_ms = (time.time() - start) * 1000
        return result

    async def _execute_task(self, task: str, context: Dict[str, Any]) -> AgentOutput:
        """Execute the report formatting and synthesis"""

        prompt = f"""
        TASK: Synthesize the following Deal Analysis into a concise, decision-useful executive summary JSON block.
        DEAL ID: {context.get("deal_id")}
        CURATED EVIDENCE PACK (the only approved fact base; data points include period, basis, and source IDs):
        {json.dumps(context.get("evidence_brief", {}), indent=2)}
        Use only the curated evidence pack above. Prefer 2-4 strong takeaways over coverage for its own sake.
        Every numeric claim must match a provided data point and retain its period. For derived comparisons,
        label them as derived and state the inputs/periods. Use source IDs exactly as supplied. Do not quote
        uncurated fields. If the evidence does not answer the deal question, say what is
        not established and name the exact evidence needed next. Return the strict JSON payload.
        """

        try:
            # We enforce JSON output directly from the LLM endpoint or by pure parsing
            result = await self.generate_with_routed_fallback(prompt, self.system_prompt)
            data = self._parse_json_result(result)

            return AgentOutput(
                success=True,
                data=data,
                reasoning="Synthesized agent outputs into a McKinsey-style SCQA structured JSON payload.",
                confidence=0.0,
            )
        except Exception as e:
            self.logger.error("Business Analyst synthesis failed", error=str(e))
            return AgentOutput(
                success=False,
                data={"error": str(e)},
                reasoning=f"Failed to generate structured synthesis: {str(e)}",
                confidence=0.0,
            )

    def _parse_json_result(self, result: Any) -> Dict[str, Any]:
        """Parse JSON response from LLM, handling potential markdown blocks."""
        content = (
            result.get("content", "").strip()
            if isinstance(result, dict)
            else str(result)
        )

        # Strip markdown code blocks if present
        if content.startswith("```"):
            import re

            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
            if match:
                content = match.group(1)

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Fallback for partial/corrupted JSON
            self.logger.warning(
                "failed_to_parse_ba_json", content_snippet=content[:100]
            )
            raise ValueError("Business Analyst response was not valid JSON.")
