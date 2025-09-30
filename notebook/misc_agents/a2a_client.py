from __future__ import annotations

import argparse
import asyncio
import logging
import json
from typing import Optional, Any, Dict
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300


def create_message(*, role: Role = Role.user, text: str) -> Message:
    return Message(
        kind="message",
        role=role,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )


def _to_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    if isinstance(obj, dict):
        return obj
    # fallback best-effort
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}


def _text_from_parts(parts) -> str:
    out: list[str] = []
    for p in parts or []:
        pd = p.model_dump(exclude_none=True) if hasattr(p, "model_dump") else (p if isinstance(p, dict) else None)
        if not isinstance(pd, dict):
            continue
        kind = pd.get("kind")
        if kind == "text" and pd.get("text"):
            out.append(pd["text"])
        elif kind == "json" and pd.get("json") is not None:
            try:
                out.append(json.dumps(pd["json"]))
            except Exception:
                out.append(str(pd["json"]))
    return "".join(out).strip()


def extract_text_from_message(msg: Message) -> Optional[str]:
    md = _to_dict(msg)
    return _text_from_parts(md.get("parts", [])) or None


def extract_text_from_task(task) -> Optional[str]:
    td = _to_dict(task)
    # Prefer artifacts[name=agent_response]
    artifacts = td.get("artifacts") or []
    chosen = None
    for a in artifacts:
        ad = a if isinstance(a, dict) else _to_dict(a)
        if ad.get("name") == "agent_response":
            chosen = ad
            break
    if chosen is None and artifacts:
        last = artifacts[-1]
        chosen = last if isinstance(last, dict) else _to_dict(last)
    if chosen:
        text = _text_from_parts(chosen.get("parts", []))
        if text:
            return text
    # Fallback: try history last agent message
    history = td.get("history") or []
    for item in reversed(history):
        id_ = item if isinstance(item, dict) else _to_dict(item)
        if id_.get("role") == "agent":
            text = _text_from_parts(id_.get("parts", []))
            if text:
                return text
    return None


async def send_message(message: str, base_url: str, streaming: bool = False) -> Optional[str]:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as httpx_client:
        # Resolve agent card
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()

        # Create client
        config = ClientConfig(httpx_client=httpx_client, streaming=streaming)
        client = None
        try:
            client = ClientFactory(config).create(agent_card)
        except Exception as e:
            logger.info("No compatible JSON-RPC transport; falling back to HTTP. (%s)", e)

        # Send
        msg = create_message(text=message)
        if client is not None:
            text_out: list[str] = []
            async for event in client.send_message(msg):
                if isinstance(event, Message):
                    text = extract_text_from_message(event)
                    if text:
                        text_out.append(text)
                elif isinstance(event, tuple) and len(event) == 2:
                    task, _update = event
                    text = extract_text_from_task(task)
                    if text:
                        text_out.append(text)
                else:
                    # Try generic extraction from unknown payloads
                    try:
                        text = extract_text_from_message(event)  # type: ignore[arg-type]
                    except Exception:
                        text = None
                    if not text:
                        try:
                            text = extract_text_from_task(event)  # type: ignore[arg-type]
                        except Exception:
                            text = None
                    if text:
                        text_out.append(text)
            return "".join(text_out) if text_out else None

        # HTTP fallback for agents that expose /a2a/messages (non-JSONRPC)
        http_payload: Dict[str, Any] = {
            "kind": "message",
            "role": "user",
            "parts": [{"kind": "text", "text": message}],
        }
        r = await httpx_client.post(base_url.rstrip("/") + "/a2a/messages", json=http_payload)
        r.raise_for_status()
        resp = r.json()
        if isinstance(resp, dict) and resp.get("kind") == "message":
            return _text_from_parts(resp.get("parts", [])) or None
        return json.dumps(resp)


def main():
    parser = argparse.ArgumentParser(description="Simple A2A client for local agents")
    parser.add_argument("--base-url", required=True, help="Agent base URL, e.g., http://127.0.0.1:9003")
    parser.add_argument("--message", required=True, help="Message to send")
    parser.add_argument("--stream", action="store_true", help="Enable client streaming mode")
    args = parser.parse_args()

    out = asyncio.run(send_message(args.message, args.base_url, args.stream))
    if out:
        print(out)
    else:
        print("<no text response>")


if __name__ == "__main__":
    main()
