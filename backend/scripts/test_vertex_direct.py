import asyncio
import os
import sys

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.llm.vertex_client import VertexClient

async def test_vertex():
    # Use the API key provided by the user in the context
    api_key = os.environ.get("VERTEX_API_KEY", "")
    model = "gemini-2.5-flash-lite"
    
    print(f"Testing Vertex AI integration with model: {model}")
    print(f"API Key: {api_key[:5]}...{api_key[-5:]}")
    
    client = VertexClient(model=model, api_key=api_key)
    
    # Override base_url to match exact user request if needed, 
    # but let's try the client's default first (us-central1)
    # The user's curl used: https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash-lite
    # If us-central1 fails, we can try global.
    
    prompt = "Explain how AI works in a few words"
    
    try:
        print("\n--- Testing Single Generation ---")
        result = await client.generate(prompt=prompt)
        print(f"Response: {result.get('content')}")
        
        print("\n--- Testing Stream Generation ---")
        print("Response: ", end="", flush=True)
        async for chunk in client.generate_stream(prompt=prompt):
            print(chunk, end="", flush=True)
        print("\n")
        
        print("SUCCESS: Vertex AI API is working correctly!")
        
    except Exception as e:
        print(f"\nFAILED: Vertex AI API test failed with error: {str(e)}")
        
        # Try global endpoint fallback in test if regional fails
        if "us-central1" in client.base_url:
            print("\nRetrying with global endpoint (aiplatform.googleapis.com)...")
            client.base_url = f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}"
            try:
                result = await client.generate(prompt=prompt)
                print(f"Response: {result.get('content')}")
                print("SUCCESS: Working with global endpoint!")
            except Exception as e2:
                print(f"FAILED: Global endpoint also failed: {str(e2)}")

if __name__ == "__main__":
    asyncio.run(test_vertex())
