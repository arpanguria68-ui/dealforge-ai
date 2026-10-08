"""Embedded SQLite knowledge graph: the default backend replacing Neo4j."""

import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.core.knowledge_graph.graph_store import NullGraphStore, SQLiteGraphStore


@pytest.fixture
def store(tmp_path):
    s = SQLiteGraphStore(str(tmp_path / "kg.db"))
    yield s
    s.close()


@pytest.mark.asyncio
async def test_agent_findings_round_trip(store):
    await store.initialize_deal("d1", "Acme Buyout", "software")
    await store.add_entity("d1", "financial_analyst_revenue", "Metric", {"value": 120.5, "agent": "financial_analyst"})
    await store.add_entity("d1", "Globex", "company", {"role": "competitor"})
    await store.add_entity("d1", "Mystery", "Spaceship", {})
    await store.add_risk("d1", "Customer concentration", 8, "Commercial", "Top client is 40% of revenue")
    await store.add_risk("d1", "Key person", 5, "Operational")

    summary = await store.deal_summary("d1")
    assert summary["deal"]["name"] == "Acme Buyout"
    assert summary["counts"] == {"Company": 1, "Entity": 1, "Metric": 1, "Risk": 2}
    assert [r["name"] for r in summary["top_risks"]] == ["Customer concentration", "Key person"]
    metrics = await store.query_current_facts("d1", "Metric")
    assert metrics[0]["properties"] == {"value": 120.5, "agent": "financial_analyst"}
    assert [r["name"] for r in await store.get_risks("d1", min_severity=6)] == ["Customer concentration"]


@pytest.mark.asyncio
async def test_nodes_are_scoped_per_deal(store):
    """Neo4j MERGEd on (label, name) globally, so deals overwrote each other's metrics."""
    await store.add_entity("d1", "financial_analyst_revenue", "Metric", {"value": 100})
    await store.add_entity("d2", "financial_analyst_revenue", "Metric", {"value": 999})

    d1 = await store.query_current_facts("d1", "Metric")
    assert d1[0]["properties"]["value"] == 100
    assert (await store.query_current_facts("d2", "Metric"))[0]["properties"]["value"] == 999


@pytest.mark.asyncio
async def test_properties_merge_and_temporal_validity(store):
    now = datetime.now(timezone.utc)
    await store.add_entity("d1", "Acme", "Company", {"hq": "NYC"})
    await store.add_entity("d1", "Acme", "Company", {"employees": 50})
    await store.add_entity("d1", "Old CEO", "Person", {"valid_until": (now - timedelta(days=1)).isoformat()})
    await store.add_entity("d1", "Future deal", "Company", {"valid_from": (now + timedelta(days=30)).isoformat()})

    facts = await store.query_current_facts("d1")
    assert [f["name"] for f in facts] == ["Acme"]
    assert facts[0]["properties"] == {"hq": "NYC", "employees": 50}


@pytest.mark.asyncio
async def test_writes_before_deal_init_are_kept_and_bad_names_ignored(store):
    await store.add_risk("d9", "Litigation", 7, "Legal")
    await store.add_entity("d9", None, "Company", {})
    assert (await store.get_deal("d9"))["deal_id"] == "d9"
    assert [f["name"] for f in await store.query_current_facts("d9")] == ["Litigation"]


@pytest.mark.asyncio
async def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "kg.db")
    first = SQLiteGraphStore(path)
    await first.add_risk("d1", "FX exposure", 4, "Financial")
    first.close()
    second = SQLiteGraphStore(path)
    assert (await second.get_risks("d1"))[0]["name"] == "FX exposure"
    second.close()


def test_backend_selection_and_neo4j_not_required(monkeypatch, tmp_path):
    from app.core.knowledge_graph import service

    monkeypatch.setenv("KG_SQLITE_PATH", str(tmp_path / "kg.db"))
    monkeypatch.delenv("KG_BACKEND", raising=False)
    assert isinstance(service.build_knowledge_graph(), SQLiteGraphStore)
    assert isinstance(service.build_knowledge_graph("off"), NullGraphStore)
    # Building the default backend must not import the neo4j driver.
    monkeypatch.setitem(sys.modules, "neo4j", None)
    assert isinstance(service.build_knowledge_graph("sqlite"), SQLiteGraphStore)


@pytest.mark.asyncio
async def test_neo4j_backend_backs_off_instead_of_redialling(monkeypatch):
    from app.core.knowledge_graph.neo4j_client import Neo4jClient

    attempts = []

    class _Driver:
        async def verify_connectivity(self):
            attempts.append(1)
            raise ConnectionError("refused")

    class _GraphDatabase:
        @staticmethod
        def driver(uri, auth):
            return _Driver()

    monkeypatch.setitem(sys.modules, "neo4j", type(sys)("neo4j"))
    sys.modules["neo4j"].AsyncGraphDatabase = _GraphDatabase
    client = Neo4jClient(uri="bolt://nowhere:7687")
    for _ in range(5):
        assert await client.run_query("RETURN 1") == []
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_graphrag_answers_from_store_facts(store, monkeypatch):
    from app.core.search import graphrag

    prompts = []

    class _Gateway:
        async def call(self, **kwargs):
            prompts.append(kwargs["prompt"])
            return {"content": "Customer concentration (severity 8)."}

    monkeypatch.setattr(graphrag, "get_llm_gateway", lambda: _Gateway())
    await store.add_risk("d1", "Customer concentration", 8, "Commercial")
    rag = graphrag.InsightForgeGraphRAG(store)

    out = await rag.answer_question("What is the top risk?", "d1")
    assert out["answer"] == "Customer concentration (severity 8)."
    assert "Customer concentration" in prompts[0]
    empty = await rag.answer_question("Anything?", "nope")
    assert "No relevant information" in empty["answer"] and len(prompts) == 1
