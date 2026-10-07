import os
import httpx
import asyncio
import json

API_KEY = os.environ.get("VERTEX_API_KEY", "")
MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite"
]

async def test_model(model):
    url = f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:streamGenerateContent?key={API_KEY}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "generationConfig": {"maxOutputTokens": 5}
    }
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                print(f"✅ {model}: SUCCESS")
                return True
            else:
                try:
                    error_data = resp.json()
                    error = error_data.get("error", {}).get("message", str(error_data))
                except:
                    error = resp.text[:100]
                print(f"❌ {model}: FAILED (HTTP {resp.status_code}) - {error}")
                return False
    except Exception as e:
        print(f"⚠️ {model}: ERROR - {type(e).__name__}: {str(e)}")
        return False

async def main():
    print(f"Testing API Key: {API_KEY[:10]}...")
    for model in MODELS:
        await test_model(model)
    print("Test complete.")

if __name__ == "__main__":
    asyncio.run(main())
