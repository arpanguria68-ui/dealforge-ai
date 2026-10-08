"""
AI/Tech Diligence Tools for DealForge AI.

Specialized tools to assess AI/ML tech stacks, model defensibility,
and quantify AI-driven revenue uplift.
"""

from typing import Dict, Any, Optional
import structlog
from app.core.tools.tool_router import BaseTool, ToolResult
from app.core.tools.grounded_extraction import EXTRACTED_NOTE, HEURISTIC_NOTE, extract_grounded, pick

logger = structlog.get_logger(__name__)


class AIStackScannerTool(BaseTool):
    """Extracts a quote-backed AI/ML tech stack map from technical text."""

    output_quality = "extracted"

    def __init__(self):
        super().__init__(
            name="ai_stack_scanner",
            description=(
                "Reads technical documents or text summaries and extracts the AI/ML stack "
                "(models, frameworks, cloud, databases, RAG components), each component "
                "backed by a verbatim quote."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "tech_summary_text": {
                    "type": "string",
                    "description": "Text body containing the target's technical documentation.",
                },
            },
            "required": ["tech_summary_text"],
        }

    COMPONENT_TYPES = ("model", "framework", "infrastructure", "database", "rag", "other")
    # Canonical names the downstream defensibility scorer keys on.
    CANONICAL = {
        "pytorch": "PyTorch", "torch": "PyTorch", "tensorflow": "TensorFlow",
        "aws": "AWS", "amazon web services": "AWS", "gcp": "GCP", "google cloud": "GCP",
        "azure": "Azure", "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
        "mongodb": "MongoDB", "mongo": "MongoDB", "pinecone": "Vector DB",
        "milvus": "Vector DB", "chroma": "Vector DB", "weaviate": "Vector DB", "pgvector": "Vector DB",
    }

    async def execute(self, tech_summary_text: str = "", **kwargs) -> ToolResult:
        items = await extract_grounded(
            "ai_stack_scanner",
            tech_summary_text,
            "List the technology components the text says the company uses (models, ML "
            "frameworks, cloud/infrastructure, databases, retrieval/RAG components).",
            {
                "component_type": "one of " + "|".join(self.COMPONENT_TYPES),
                "name": "component name as written",
            },
            max_items=40,
        )
        if items is None:
            scorecard = self._keyword_scorecard(tech_summary_text)
            scorecard.update(data_quality="heuristic", method_note=HEURISTIC_NOTE)
            return ToolResult(success=True, data=scorecard)

        buckets: Dict[str, list] = {t: [] for t in self.COMPONENT_TYPES}
        components = []
        for i in items:
            kind = pick(i.get("component_type"), self.COMPONENT_TYPES, "other")
            raw = str(i.get("name") or "").strip()[:80]
            if not raw:
                continue
            name = self.CANONICAL.get(raw.lower(), raw)
            if name not in buckets[kind]:
                buckets[kind].append(name)
            components.append({"type": kind, "name": name, "quote": i["quote"]})
        models = buckets["model"] + buckets["framework"]
        scorecard = {
            "models_and_frameworks": models or ["Unknown / Custom Models"],
            "infrastructure": buckets["infrastructure"] or ["Unknown / On-Prem"],
            "databases": buckets["database"] or ["Unknown Database"],
            "has_rag_components": bool(buckets["rag"]) or "Vector DB" in buckets["database"],
            "overall_stack_age_assessment": "Modern" if models else "Legacy / Unclear",
            "components": components,
            "data_quality": "extracted",
            "method_note": EXTRACTED_NOTE,
        }
        return ToolResult(success=True, data=scorecard)

    @staticmethod
    def _keyword_scorecard(tech_summary_text: str) -> Dict[str, Any]:
        text = (tech_summary_text or "").lower()
        models = [label for key, label in (("llama", "Llama"), ("gpt", "GPT-based"),
                                           ("pytorch", "PyTorch"), ("tensorflow", "TensorFlow")) if key in text]
        infra = [label for key, label in (("aws", "AWS"), ("gcp", "GCP"), ("azure", "Azure")) if key in text]
        db = [label for key, label in (("postgres", "PostgreSQL"), ("mongo", "MongoDB")) if key in text]
        if "pinecone" in text or "milvus" in text or "chroma" in text:
            db.append("Vector DB")
        return {
            "models_and_frameworks": models or ["Unknown / Custom Models"],
            "infrastructure": infra or ["Unknown / On-Prem"],
            "databases": db or ["Unknown Database"],
            "has_rag_components": "rag" in text or "retrieval" in text,
            "overall_stack_age_assessment": "Modern" if models else "Legacy / Unclear",
        }


class ModelDefensibilityScorerTool(BaseTool):
    """Scores IP protection, scalability, and obsolescence risk."""

    output_quality = "heuristic"

    def __init__(self):
        super().__init__(
            name="model_defensibility_scorer",
            description=(
                "Scores the target's IP protection and scalability based on stack "
                "metadata. Returns a 0-100 score and specific risk factors."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "stack_metadata": {
                    "type": "object",
                    "description": "JSON object from AIStackScannerTool containing stack details.",
                },
            },
            "required": ["stack_metadata"],
        }

    async def execute(self, stack_metadata: Dict = None, **kwargs) -> ToolResult:
        stack = stack_metadata or {}
        score = 50
        risk_factors = []

        models = stack.get("models_and_frameworks", [])

        if "PyTorch" in models or "TensorFlow" in models:
            score += 20
        else:
            risk_factors.append("No standard ML training framework detected.")

        if stack.get("has_rag_components"):
            score += 15
        else:
            risk_factors.append("No modern RAG/LLM architecture components found.")

        if "Unknown / On-Prem" in stack.get("infrastructure", []):
            risk_factors.append(
                "Scale limitations due to missing cloud infrastructure."
            )
            score -= 10
        else:
            score += 15

        if "Unknown Database" in stack.get("databases", []):
            risk_factors.append("Unclear data storage strategy.")

        # Cap score
        score = min(100, max(0, score))

        return ToolResult(
            success=True,
            data={
                "defensibility_score": score,
                "risk_factors": risk_factors,
                "assessment_level": (
                    "High" if score >= 80 else ("Medium" if score >= 50 else "Low")
                ),
            },
        )


class AIValueQuantifierTool(BaseTool):
    """Estimates AI-driven revenue uplift potential."""

    output_quality = "heuristic"

    def __init__(self):
        super().__init__(
            name="ai_value_quantifier",
            description=(
                "Estimates AI-driven revenue uplift potential given current target revenue "
                "and an AI defensibility score."
            ),
        )

    def get_parameters_schema(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "current_revenue": {
                    "type": "number",
                    "description": "Target's current baseline annual revenue.",
                },
                "defensibility_score": {
                    "type": "number",
                    "description": "Defensibility score (0-100) from ModelDefensibilityScorerTool.",
                },
            },
            "required": ["current_revenue", "defensibility_score"],
        }

    async def execute(
        self, current_revenue: float = 0.0, defensibility_score: float = 0.0, **kwargs
    ) -> ToolResult:
        if current_revenue <= 0:
            return ToolResult(success=False, data=None, error="Revenue must be > 0.")

        # Simplified uplift logic: higher score = higher % uplift potential
        base_uplift_pct = (defensibility_score / 100.0) * 0.15  # Max 15% revenue uplift

        low_uplift = current_revenue * (base_uplift_pct * 0.5)
        base_uplift = current_revenue * base_uplift_pct
        high_uplift = current_revenue * (base_uplift_pct * 1.5)

        confidence = (
            "High"
            if defensibility_score >= 80
            else ("Medium" if defensibility_score >= 50 else "Low")
        )

        estimation = {
            "low_value_uplift": round(low_uplift, 2),
            "base_value_uplift": round(base_uplift, 2),
            "high_value_uplift": round(high_uplift, 2),
            "confidence_level": confidence,
            "note": f"Estimated based on {defensibility_score}/100 defensibility score.",
        }

        return ToolResult(success=True, data=estimation)
