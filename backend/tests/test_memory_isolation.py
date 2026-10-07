import pytest


@pytest.mark.asyncio
async def test_agent_memory_retrieval_inherits_active_deal_scope():
    from app.agents.financial_analyst import FinancialAnalystAgent

    captured = {}

    class Memory:
        async def query(self, query, **kwargs):
            captured.update(kwargs)
            return []

    agent = FinancialAnalystAgent.__new__(FinancialAnalystAgent)
    agent.memory = Memory()
    agent._current_context = {"deal_id": "deal-current"}

    await agent.retrieve_context("financial risks", top_k=3)

    assert captured == {"top_k": 3, "filters": {"deal_id": "deal-current"}}


@pytest.mark.asyncio
async def test_due_diligence_cross_deal_memory_is_opt_in(monkeypatch):
    from app.agents.due_diligence_agent import CommercialDueDiligenceAgent

    calls = []

    class MemoryService:
        async def read_memory(self, **kwargs):
            calls.append(kwargs)
            return [{"content": "prior deal facts"}]

    monkeypatch.setattr(
        "app.core.memory.memory_service.get_memory_service",
        lambda: MemoryService(),
    )
    agent = CommercialDueDiligenceAgent.__new__(CommercialDueDiligenceAgent)

    assert await agent._get_cross_deal_context({"deal_id": "deal-current"}) == []
    assert calls == []
    assert await agent._get_cross_deal_context({
        "deal_id": "deal-current", "allow_cross_deal_patterns": True,
    }) == [{"content": "prior deal facts"}]
    assert len(calls) == 1
