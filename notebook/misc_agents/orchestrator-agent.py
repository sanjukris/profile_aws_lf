from __future__ import annotations

import argparse
import logging
import os
import json
import uuid

from dotenv import load_dotenv

from strands import Agent, tool
from strands.models.anthropic import AnthropicModel
from strands_tools.a2a_client import A2AClientToolProvider

# For HTTP fallback to non-JSONRPC agents
import asyncio
import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message as A2AMessage, Part as A2APart, Role as A2ARole, TextPart as A2ATextPart
import uuid
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Target Profile A2A server (Strands JSON-RPC)
PROFILE_AGENT_URL = os.getenv("PROFILE_AGENT_URL", "http://127.0.0.1:9004/")


ORCHESTRATOR_SYSTEM = (
    "You are the Orchestrator Agent. You have one tool 'call_profile_agent' that calls the remote Profile A2A agent. "
    "For profile-related requests (email, address, preferences), ALWAYS call this tool with the user's question and "
    "RETURN ONLY the tool's raw output without rephrasing. If the user did not provide a member_id, ask for it."
)


class A2AAgentTool:
    """Wrap a remote A2A agent as a Strands tool with pre-resolved agent card.

    - Avoids repeated discovery calls by caching the agent card at init.
    - Supports both JSONRPC and HTTP transports based on the card's preferredTransport.
    - Returns the remote agent's structured JSON/text exactly as provided.
    """

    def __init__(self, agent_url: str, agent_name: str = "Profile Agent"):
        self.agent_url = agent_url.rstrip("/") + "/"
        self.agent_name = agent_name
        self.agent_card = None
        self.preferred_transport = "JSONRPC"
        # Resolve once (async) and cache the card
        self._resolve_card()

    def _resolve_card(self):
        async def _resolve(url: str):
            async with httpx.AsyncClient(timeout=15) as httpx_client:
                resolver = A2ACardResolver(httpx_client=httpx_client, base_url=url)
                return await resolver.get_agent_card()

        card = asyncio.run(_resolve(self.agent_url))
        # Cache raw card and preferred transport
        self.agent_card = card
        pt = None
        if hasattr(card, "model_dump"):
            data = card.model_dump(exclude_none=True)
            pt = data.get("preferredTransport") or data.get("preferred_transport")
        elif isinstance(card, dict):
            pt = card.get("preferredTransport") or card.get("preferred_transport")
        else:
            pt = getattr(card, "preferred_transport", None) or getattr(card, "preferredTransport", None)
        self.preferred_transport = (pt or "JSONRPC").upper()

    def _parts_to_text(self, parts) -> str:
        out: list[str] = []
        for p in parts or []:
            if isinstance(p, dict):
                kind = p.get("kind")
                if kind == "text" and p.get("text"):
                    out.append(p["text"])
                elif kind == "json" and p.get("json") is not None:
                    out.append(json.dumps(p["json"]))
            else:
                # a2a types
                try:
                    pd = p.model_dump(exclude_none=True)
                except Exception:
                    pd = {}
                kind = pd.get("kind")
                if kind == "text" and pd.get("text"):
                    out.append(pd["text"])
                elif kind == "json" and pd.get("json") is not None:
                    out.append(json.dumps(pd["json"]))
        return "\n".join(out).strip()

    async def _call_http(self, message: str) -> str:
        payload = {
            "kind": "message",
            "role": "user",
            "parts": [{"kind": "text", "text": message}],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(self.agent_url + "a2a/messages", json=payload)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("kind") == "message":
                return self._parts_to_text(data.get("parts", []))
            return json.dumps(data)

    async def _call_jsonrpc(self, message: str) -> str:
        async with httpx.AsyncClient(timeout=30) as httpx_client:
            config = ClientConfig(httpx_client=httpx_client, streaming=False)
            client = ClientFactory(config).create(self.agent_card)

            msg = A2AMessage(
                kind="message",
                role=A2ARole.user,
                parts=[A2APart(A2ATextPart(kind="text", text=message))],
                message_id=uuid.uuid4().hex,
            )

            async for event in client.send_message(msg):
                # Message
                try:
                    from a2a.types import Message as _Msg
                    if isinstance(event, _Msg):
                        return self._parts_to_text(event.parts)
                except Exception:
                    pass
                # Task + UpdateEvent
                if isinstance(event, tuple) and len(event) == 2:
                    task, _ = event
                    try:
                        td = task.model_dump(exclude_none=True)
                    except Exception:
                        td = {}
                    artifacts = td.get("artifacts") or []
                    chosen = None
                    for a in artifacts:
                        if isinstance(a, dict) and a.get("name") == "agent_response":
                            chosen = a
                            break
                    if chosen is None and artifacts and isinstance(artifacts[-1], dict):
                        chosen = artifacts[-1]
                    if chosen:
                        return self._parts_to_text(chosen.get("parts", []))
                    # Fallback: history
                    for item in reversed(td.get("history") or []):
                        if isinstance(item, dict) and item.get("role") == "agent":
                            return self._parts_to_text(item.get("parts", []))
            return ""

    async def invoke(self, message: str) -> str:
        """Programmatic invocation that returns the remote agent's raw output."""
        try:
            if self.preferred_transport == "HTTP":
                return await self._call_http(message)
            return await self._call_jsonrpc(message)
        except Exception as e:
            return f"Error contacting {self.agent_name}: {str(e)}"

    @tool
    async def call_profile_agent(self, message: str) -> str:
        """Tool entrypoint that proxies to invoke()."""
        return await self.invoke(message)


def build_agent(profile_agent_url: str) -> Agent:
    # Prefer Anthropic to avoid accidental Bedrock default when credentials are missing
    anth_api_key = os.getenv("ANTHROPIC_API_KEY")
    model = AnthropicModel(
        client_args={"api_key": anth_api_key},
        model_id="claude-3-7-sonnet-20250219",
        max_tokens=512,
        params={"temperature": 0},
    )

    # Pre-resolve and wrap the target agent as a tool
    a2a_tool = A2AAgentTool(profile_agent_url, agent_name="Profile Agent")

    return Agent(
        name="Orchestrator Agent",
        description="Routes profile questions to Profile A2A agent via A2A tool wrapper",
        system_prompt=ORCHESTRATOR_SYSTEM,
        tools=[a2a_tool.call_profile_agent],
        model=model,
        callback_handler=None,
    )


def main():
    parser = argparse.ArgumentParser(description="Orchestrator Agent that calls Profile A2A agent via tools")
    parser.add_argument(
        "--question",
        required=False,
        default="show address for member 378477398",
        help="User query to route to the Profile agent",
    )
    args = parser.parse_args()

    # Direct tool invocation to ensure exact structured output (no LLM paraphrase)
    tool_wrapper = A2AAgentTool(PROFILE_AGENT_URL, agent_name="Profile Agent")
    response = asyncio.run(tool_wrapper.invoke(args.question))
    logger.info("Response: %s", response)
    print(response)


if __name__ == "__main__":
    main()
