import asyncio

import aiosqlite

from app.core.quality.agent_quality_store import AgentQualityStore


def test_deal_ratings_are_persisted_and_legacy_schema_is_migrated(tmp_path):
    db_path = tmp_path / "agent_quality.db"

    async def scenario():
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE agent_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    deal_context_hash TEXT NOT NULL,
                    action_payload TEXT NOT NULL,
                    score REAL,
                    feedback TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

        store = AgentQualityStore(str(db_path))
        await store.initialize()
        await store.log_action(
            agent_name="market_researcher",
            task_type="user_rated",
            deal_context={"deal_id": "deal-123"},
            action_payload={"rating": 4, "feedback": "Useful sources"},
            score=0.8,
            feedback="Useful sources",
        )
        return await store.get_deal_ratings("deal-123")

    ratings = asyncio.run(scenario())
    assert len(ratings) == 1
    assert ratings[0]["agent_type"] == "market_researcher"
    assert ratings[0]["rating"] == 4
    assert ratings[0]["feedback"] == "Useful sources"
