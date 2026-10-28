import asyncio
import os
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(__file__))

from ai_engine.clients.factory import LLMClientFactory

async def main():
    client = LLMClientFactory.get_client()
    print("=" * 50)
    print("ACTIVE CLIENT CLASS:", type(client).__name__)
    print("BASE URL:", getattr(client, "base_url", "N/A"))
    print("MODEL:", getattr(client, "model", "N/A"))
    print("=" * 50)
    
    print("Checking availability...")
    available = await client.is_available()
    print("REACHABLE:", available)
    
    if available:
        print("\nSending test prompt to Qwen-27B on STPI...")
        try:
            response = await client.generate("Reply in 5 words or less: Handshake successful!")
            print("AI RESPONSE:", response)
            print("\n>>> SUCCESS: AI Engine Infrastructure is 100% operational! <<<")
        except Exception as e:
            print("ERROR during generate:", e)
    else:
        print("WARNING: Client is not reachable at the configured URL.")

if __name__ == "__main__":
    asyncio.run(main())
