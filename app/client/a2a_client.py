from __future__ import annotations
import asyncio
import json
import httpx

BASE_URL = "http://127.0.0.1:9000"


async def fetch_agent_card(client: httpx.AsyncClient):
    r = await client.get(f"{BASE_URL}/a2a/agent-card")
    r.raise_for_status()
    print("AGENT CARD:")
    print(json.dumps(r.json(), indent=2))


async def send_message(client: httpx.AsyncClient, text: str):
    payload = {
        "kind": "message",
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
    }
    r = await client.post(f"{BASE_URL}/a2a/messages", json=payload)
    r.raise_for_status()
    print("RESPONSE:")
    print(json.dumps(r.json(), indent=2))


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        await fetch_agent_card(client)

        # Email and address
        await send_message(
            client,
            "show my email and mailing address for member 378477398",
        )

        # Preferences
        await send_message(
            client,
            "show my contact preferences for member 378477398",
        )


if __name__ == "__main__":
    asyncio.run(main())
