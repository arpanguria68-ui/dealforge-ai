import asyncio
import pytest
import json
from app.core.messaging.message_bus import get_message_bus, AgentMessage
from app.agents.base import BaseAgent, AgentOutput
from app.core.redis_store import RedisStore

class MockAgent(BaseAgent):
    name = "mock_agent"
    
    async def generate_issue_tree(self, task, context):
        from app.agents.base import IssueTreeNode
        return IssueTreeNode(id="root", hypothesis="test", sub_branches=[])
        
    def validate_mece(self, tree):
        return True, []
        
    async def retrieve_context(self, query, top_k=5, deal_id=None):
        return []
        
    async def reflect(self, task, output):
        return 0.9
        
    async def run(self, task: str, context: dict = None) -> AgentOutput:
        return AgentOutput(success=True, data={"test": "data"}, reasoning="Test reasoning", confidence=0.9)

@pytest.mark.asyncio
async def test_message_bus_publish_retrieval():
    print("Starting test_message_bus_publish_retrieval...")
    bus = get_message_bus()
    deal_id = "test_deal_123"
    
    # 1. Clean up potential old messages
    redis = RedisStore.get_instance()
    await redis.client.delete(f"messages:{deal_id}")
    
    # 2. Publish a message
    msg = AgentMessage(
        sender="agent_a",
        msg_type="insight_discovered",
        payload={"summary": "Found a huge margin expansion opportunity"},
        deal_id=deal_id
    )
    await bus.publish(msg)
    
    # 3. Retrieve messages
    messages = await bus.get_messages(deal_id)
    print(f"Retrieved {len(messages)} messages")
    assert len(messages) == 1
    assert messages[0].sender == "agent_a"
    assert "margin expansion" in messages[0].payload["summary"]
    print("test_message_bus_publish_retrieval passed!")

@pytest.mark.asyncio
async def test_agent_integration():
    print("Starting test_agent_integration...")
    deal_id = "test_deal_456"
    agent_a = MockAgent()
    agent_b = MockAgent()
    agent_b.name = "agent_b"
    
    # 1. Agent A runs and discovers something
    print("Running Agent A...")
    await agent_a.run_with_structure("Find insights", context={"deal_id": deal_id})
    
    # 2. Agent B runs and should see Agent A's insight in its context
    print("Checking Agent B context awareness...")
    bus = get_message_bus()
    msgs = await bus.get_messages(deal_id)
    print(f"Agent B sees {len(msgs)} messages")
    assert any(m.sender == "mock_agent" for m in msgs)
    print("test_agent_integration passed!")

async def main():
    try:
        await test_message_bus_publish_retrieval()
        await test_agent_integration()
        print("ALL VERIFICATION TESTS PASSED!")
    except Exception as e:
        print(f"VERIFICATION FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        exit(1)

if __name__ == "__main__":
    asyncio.run(main())
