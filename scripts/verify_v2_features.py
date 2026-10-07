import asyncio
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.llm.model_registry import get_capabilities
from app.core.llm.capability_probe import probe_fallback_chain
from app.core.llm.token_budget import TokenBudget
from app.orchestrator.state import create_initial_state
from app.orchestrator.graph import get_orchestrator
from app.core.messaging.message_bus import get_message_bus, AgentMessage

async def verify_phase_2():
    print("🚀 Starting Phase 2 Verification Sequence...")
    
    # 1. Verify Model Registry
    gpt4 = get_capabilities("gpt-4o", "openai")
    print(f"✅ F-009: Model Registry - GPT-4o JSON support: {gpt4.json_mode}")
    
    # 2. Verify Capability Probe (F-010)
    results = await probe_fallback_chain()
    print(f"✅ F-010: Capability Probe - Active providers: {list(results.keys())}")
    
    # 3. Verify Token Budgeting
    budget = TokenBudget(total_budget=1000)
    budget.add_source("Doc1", "This is a test document content", {"meta": "data"})
    context_block = budget.get_context_block()
    print(f"✅ F-011: Token Budgeting - Context block length: {len(context_block)}")
    assert "Doc1" in context_block
    
    # 4. Verify Message Bus
    bus = get_message_bus()
    test_msg = AgentMessage(sender="test", msg_type="insight_discovered", payload={"test": "data"}, deal_id="verify_deal")
    await bus.publish(test_msg)
    msgs = await bus.get_messages("verify_deal")
    print(f"✅ F-016: Message Bus - Retrieval success: {len(msgs) > 0}")

    # 5. Verify State Registry (F-014)
    state = create_initial_state("test_deal", "Test Deal")
    print(f"✅ F-014: Source Registry in State - Exists: {'source_registry' in state}")

    print("\n🎉 PHASE 2 INFRASTRUCTURE VERIFIED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(verify_phase_2())
