"""Graph-grounded Q&A over the deal knowledge graph (F-025).

The old implementation asked an LLM to write Cypher and executed it
verbatim against Neo4j (unvalidated, write-capable queries built from user
text). This version reads the deal's current facts through the store API
(parameterized, read-only) and has the LLM answer only from those facts.
"""
import json
from typing import Any, Dict, Optional

import structlog

from app.core.llm.llm_gateway import get_llm_gateway

logger = structlog.get_logger(__name__)


class InsightForgeGraphRAG:
    """Answer deal questions from the knowledge graph's current facts."""

    def __init__(self, graph_store: Any = None, provider: str = "gemini", model: Optional[str] = None):
        if graph_store is None:
            from app.core.knowledge_graph.service import get_knowledge_graph

            graph_store = get_knowledge_graph()
        self.graph = graph_store
        self.provider = provider
        self.model = model
        self.gateway = get_llm_gateway()

    async def answer_question(self, question: str, deal_id: str) -> Dict[str, Any]:
        summary = await self.graph.deal_summary(deal_id)
        facts = summary.get("facts") or []
        logger.info("graphrag_facts_loaded", deal_id=deal_id, fact_count=len(facts))
        if not facts:
            return {
                "answer": "No relevant information found in the knowledge graph for this deal.",
                "graph_results": [],
                "source": "InsightForge GraphRAG",
            }
        prompt = (
            f"Question: {question}\n\n"
            f"Deal knowledge graph facts (JSON):\n{json.dumps(facts[:60], default=str)}\n\n"
            "Answer concisely using ONLY these facts. If they do not answer the "
            "question, say what is missing."
        )
        response = await self.gateway.call(
            provider=self.provider,
            model=self.model,
            prompt=prompt,
            temperature=0.0,
        )
        return {
            "answer": str(response.get("content", "")).strip(),
            "graph_results": facts[:5],
            "counts": summary.get("counts", {}),
            "source": "InsightForge GraphRAG",
        }
