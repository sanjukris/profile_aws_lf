from __future__ import annotations
from typing import List, Optional, Dict, Any
import re, time

from fastapi import FastAPI
from pydantic import BaseModel

from app.agents.profile_agent import handle_request
from app.telemetry.tracing import (
    get_current_trace, set_current_trace, reset_current_trace, diagnostics as lf_diagnostics, test_trace as lf_test_trace
)
from dotenv import load_dotenv
load_dotenv()

from langfuse import get_client  # v3 API

app = FastAPI(title="Profile Agent A2A", version="0.1.0")

# Diagnostics endpoints (unchanged signatures)
@app.get("/telemetry/status")
async def telemetry_status() -> Dict[str, Any]:
    return lf_diagnostics()

@app.post("/telemetry/test")
async def telemetry_test() -> Dict[str, Any]:
    return lf_test_trace(name="a2a_telemetry_test", metadata={"route": "/telemetry/test"})


# ----- A2A-style payload models -----
class TextPart(BaseModel):
    kind: str = "text"
    text: Optional[str] = None


class Message(BaseModel):
    kind: str = "message"
    role: str
    parts: List[TextPart]


# ----- helpers -----
def _extract_member_id(text: str) -> str:
    """
    Find a numeric member id in the text.
    Falls back to the mock id if not found.
    """
    m = re.search(r"\b(\d{6,})\b", text or "")
    return m.group(1) if m else "378477398"


def _first_text(parts: List[TextPart]) -> str:
    for p in parts or []:
        if p.kind == "text" and p.text:
            return p.text
    return ""


# ----- endpoints -----
@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/a2a/agent-card")
async def agent_card() -> Dict[str, Any]:
    return {
        "kind": "agent_card",
        "name": "profile-agent",
        "description": "Profile Agent with mocked APIs. Returns profile overview or preferences as structured JSON.",
        "version": "0.1.0",
        "capabilities": {"streaming": False},
        "inputs": [{"kind": "message"}],
        "outputs": [{"kind": "message"}],
    }


@app.post("/a2a/messages")
async def a2a_messages(msg: Message) -> Dict[str, Any]:
    text = _first_text(msg.parts)
    member_id = _extract_member_id(text)

    # Get/reuse the singleton client & make it available to nested functions
    lf_client = get_client()  # picks up env vars
    token = set_current_trace(lf_client)

    t0 = time.perf_counter()
    try:
        # Root span for this request; makes context active for children
        with lf_client.start_as_current_span(
            name="a2a_message",
            input={"text": text, "member_id": member_id},
            metadata={"route": "/a2a/messages"},
        ) as root:
            tool_name, payload = handle_request(query=text, member_id=member_id)
            # Attach summary to root
            root.update(output={"tool": tool_name, "payload_keys": list(payload.keys())})
    finally:
        reset_current_trace(token)
    t1 = time.perf_counter()
    print(f"[timing] /a2a/messages total={(t1 - t0)*1000:.1f} ms query='{text[:80]}'")

    return {
        "kind": "message",
        "role": "assistant",
        "parts": [
            {"kind": "text", "text": tool_name},
            {"kind": "json", "json": payload},
        ],
    }
