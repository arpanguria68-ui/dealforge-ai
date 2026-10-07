"""Graph-based RAG for complex deal intelligence (F-025)."""
import json
from typing import Any, Dict, List, Optional
import structlog
from app.core.llm.llm_gateway import get_llm_gateway
from app.core.knowledge_graph.neo4j_client import Neo4jClient

logger = structlog.get_logger(__name__)

class InsightForgeGraphRAG:
    """Graph-based reasoning: traverse knowledge graph to answer complex deal questions (F-025)."""

    def __init__(self, neo4j_client: Neo4jClient, provider: str = "gemini", model: Optional[str] = None):
        self.neo4j_client = neo4j_client
        self.provider = provider
        self.model = model
        self.gateway = get_llm_gateway()

    async def answer_question(self, question: str, deal_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Answer a complex question by translating it to Cypher and executing it.
        """
        self.logger = logger.bind(question=question, deal_id=deal_id)
        self.logger.info("graphrag_query_started")

        # 1. Translate NL question to Cypher
        cypher_query = await self._decompose_to_cypher(question, deal_id)
        if not cypher_query:
            return {"answer": "I couldn't translate your question to a graph query.", "source": "graph_rag_failure"}

        # 2. Execute Cypher
        results = await self.neo4j_client.run_query(cypher_query)
        self.logger.info("cypher_executed", result_count=len(results))

        # 3. Synthesize Final Answer
        answer = await self._synthesize_answer(question, results)
        
        return {
            "answer": answer,
            "cypher": cypher_query,
            "graph_results": results[:5],  # Subset for transparency
            "source": "InsightForge GraphRAG"
        }

    async def _decompose_to_cypher(self, question: str, deal_id: Optional[str] = None) -> str:
        """Use LLM to convert a natural language question into a Cypher query."""
        deal_context = f"WHERE d.id = '{deal_id}'" if deal_id else ""
        
        prompt = f"""
        Knowledge Graph Schema:
        Nodes: Deal {{id, name, industry}}, Company {{name, type}}, Risk {{name, severity, category}}, Metric {{name, value}}, Person, Product, RegulatoryBody.
        Relationships: (Deal)-[:INVOLVES]->(Entity), (Deal)-[:HAS_RISK]->(Risk).
        
        Question: {question}
        
        Generate a Cypher query to answer this question. 
        Always include the root Deal node 'd' if possible.
        {deal_context}
        
        Return ONLY the Cypher query text. No markdown, no commentary.
        Example: MATCH (d:Deal)-[:HAS_RISK]->(r:Risk) WHERE d.id = '...' RETURN r.name, r.severity ORDER BY r.severity DESC
        """
        
        response = await self.gateway.call(
            provider=self.provider,
            model=self.model,
            prompt=prompt,
            temperature=0.0
        )
        
        cypher = response.get("content", "").strip()
        # Clean up possible markdown code blocks
        cypher = cypher.replace("```cypher", "").replace("```", "").strip()
        return cypher

    async def _synthesize_answer(self, question: str, results: List[Dict[str, Any]]) -> str:
        """Use LLM to synthesize the graph results into a readable answer."""
        if not results:
            return "No relevant information found in the knowledge graph for this question."

        prompt = f"""
        Question: {question}
        Graph Data: {json.dumps(results[:20])}

        Summarize the findings from the graph data into a concise, professional answer.
        If the data is empty or generic, explain what was found.
        """
        
        response = await self.gateway.call(
            provider=self.provider,
            model=self.model,
            prompt=prompt,
            temperature=0.3
        )
        
        return response.get("content", "").strip()
