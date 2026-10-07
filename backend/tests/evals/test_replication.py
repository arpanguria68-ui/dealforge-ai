import pytest

from app.core.quality.replication import evaluate_outputs, similarity
from app.core.quality.agent_quality_store import AgentQualityStore


def test_similarity_distinguishes_equal_and_different_outputs():
    assert similarity("same output", "same output") == 1.0
    assert similarity("revenue increased", "legal contract") < 0.5


def test_empty_replication_batch_has_defined_neutral_metrics():
    stats = evaluate_outputs([])
    assert stats == {"mean_similarity": 1.0, "min_similarity": 1.0, "pair_count": 0}


def test_identical_outputs_are_fully_reproducible():
    stats = evaluate_outputs(["a", "a", "a"])
    assert stats["mean_similarity"] == stats["min_similarity"] == 1.0
    assert stats["pair_count"] == 3


@pytest.mark.asyncio
async def test_agent_quality_store_initializes_and_roundtrips_replication(tmp_path):
    store = AgentQualityStore(db_path=str(tmp_path / "rep.db"))
    await store.initialize()
    record_id = await store.log_replication_run("financial_analyst", "dcf", ["x", "x", "x"])
    recent = await store.get_recent_replication("financial_analyst", "dcf")
    assert isinstance(record_id, int)
    assert len(recent) == 1
    assert recent[0]["outputs"] == ["x", "x", "x"]
    assert recent[0]["avg_similarity"] == recent[0]["min_similarity"] == 1.0
