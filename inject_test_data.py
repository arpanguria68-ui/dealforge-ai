import asyncio
import os
import sys
import httpx

# Add current directory to path to reach backend
sys.path.append(os.getcwd())

async def run_test():
    try:
        # 1. Create a deal via API
        async with httpx.AsyncClient(timeout=15) as client:
            deal_data = {
                "name": "TechCore Robotics",
                "target_company": "RoboSystems Ltd",
                "industry": "Robotics",
                "description": "Simulation for Agent Console verification",
                "status": "created"
            }
            r = await client.post("http://localhost:8005/api/v1/deals", json=deal_data)
            if r.status_code != 200:
                print(f"Failed to create deal: {r.text}")
                return
            deal_id = r.json()["id"]
            print(f"Created Test Deal ID: {deal_id}")

            # 2. Add some inter-agent messages to the bus
            # Since the API doesn't expose a 'send message' for external use (it's internal to agents),
            # we use the backend logic directly via the bus singleton.
            # We need to set up the environment first.
            from backend.app.orchestrator.agent_bus import get_agent_message_bus
            
            bus = get_agent_message_bus()
            
            # Message 1: Query
            msg = await bus.send_message(
                deal_id=deal_id,
                from_agent="market_researcher",
                to_agent="financial_analyst",
                subject="Market Size Verification",
                content="Can you verify if the total addressable market (TAM) for their core robotic arm product exceeds $500M?"
            )
            print("Sent message 1")
            
            # Message 2: Response
            await bus.respond_to_message(
                msg.id,
                "Confirmed. Based on recent IDC and Gartner reports, the TAM is estimated at $1.2B with a CAGR of 15% through 2028."
            )
            print("Sent response for message 1")

            # Message 3: Another query
            await bus.send_message(
                deal_id=deal_id,
                from_agent="financial_analyst",
                to_agent="legal_advisor",
                subject="IP Portfolio Check",
                content="Are there any pending litigations regarding their patent US-1234567-B2?"
            )
            print("Sent message 2 (pending)")

            print(f"\nSUCCESS: Test deal {deal_id} is now ready with simulated communication.")

    except Exception as e:
        print(f"Error during test setup: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
