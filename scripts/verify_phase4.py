"""Verification script for Phase 4: Knowledge Graph Features."""
import asyncio
import json
from app.core.knowledge_graph.neo4j_client import Neo4jClient, DealKnowledgeGraph
from app.core.knowledge_graph.ontology_service import OntologyService
from app.core.search.graphrag import InsightForgeGraphRAG

async def verify_ontology():
    print("\n--- Verifying Ontology Service (F-022) ---")
    svc = OntologyService()
    industry = "Biotech"
    brief = "Acquisition of a gene therapy startup with 3 Phase II clinical trials and 5-year patent runway."
    
    ontology = await svc.generate_ontology(industry, brief)
    print(f"Generated entities: {ontology.get('entities')}")
    print(f"Generated relationships: {len(ontology.get('relationships', []))}")
    assert "entities" in ontology
    return ontology

async def verify_graph_rag_logic():
    print("\n--- Verifying GraphRAG Logic (F-025) ---")
    # Mock Neo4j client to avoid connection error during logic test
    class MockNeo4j:
        async def run_query(self, query, params=None):
            print(f"Executing Cypher: {query}")
            return [{"r.name": "Clinical Trial Delay", "r.severity": 9}]
            
    client = MockNeo4j()
    rag = InsightForgeGraphRAG(client)
    
    question = "What are the most severe biotech risks in this deal?"
    result = await rag.answer_question(question, deal_id="test_biotech_001")
    
    print(f"GraphRAG Answer: {result['answer']}")
    print(f"Generated Cypher: {result['cypher']}")
    assert "cypher" in result
    assert "answer" in result

async def main():
    try:
        await verify_ontology()
        await verify_graph_rag_logic()
        print("\nPhase 4 Logic Verification: SUCCESS")
    except Exception as e:
        print(f"\nPhase 4 Logic Verification: FAILED - {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
