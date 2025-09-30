from __future__ import annotations

"""
Profile Strands Agent (A2A JSON‑RPC)
------------------------------------
This service exposes a Strands Agent as an A2A‑compliant JSON‑RPC server and returns
deterministic, structured JSON for profile information (email + address). It’s designed
to be fast and interoperable with other agents, while remaining faithful to the A2A
protocol so standard clients (A2ACardResolver/ClientFactory) can discover and talk to it.

Key design points for performance and compliance
- Uses Strands' A2AServer to expose a canonical agent card at
  `/.well-known/agent-card.json` and JSON‑RPC on `POST /`.
- Adds a minimal “fast‑path” middleware that intercepts the JSON‑RPC method
  `message/send` and synchronously calls a deterministic tool (get_profile_overview),
  bypassing any LLM planning loop. This is what makes it fast.
- The fast‑path constructs a valid JSON‑RPC result in the A2A shape (Message with
  role="agent" and a unique messageId), so A2A SDK clients immediately accept it.
- Host/port passed to A2AServer so the agent card advertises a reachable URL.
- Simple hardening (CORS, request ID, API key on custom endpoints) for local dev.

When to extend
- Replace the stub tool (get_profile_overview) with real downstream integrations, or
  enrich the fast‑path routing to branch to different deterministic tools by intent.
"""

import os
import re
import time
import uuid
from typing import List
import json

import uvicorn
from dotenv import load_dotenv
from fastapi import Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from strands import Agent, tool
from strands.multiagent.a2a import A2AServer

load_dotenv()


# ----- Structured profile schema -----
class NameValue(BaseModel):
    name: str
    value: str


class ProfileData(BaseModel):
    address: List[NameValue]
    email: List[NameValue]


class ProfileResponse(BaseModel):
    data: ProfileData


def extract_member_id(text: str) -> str:
    """Extract first 6+ digit sequence as member_id, else default.

    This keeps the demo robust for free‑form queries like:
    "show address for member 378477398" or "my id is 123456".
    """
    m = re.search(r"\b(\d{6,})\b", text or "")
    return m.group(1) if m else "378477398"


# Try to import existing business logic if available
try:
    from agents.profile_agent_aws import handle_request as HANDLE_REQUEST  # type: ignore
except Exception:
    HANDLE_REQUEST = None


# ----- Tool: call existing logic when available, else stub -----
@tool
def get_profile_overview(member_id: str, query: str | None = None) -> str:
    """Return structured profile JSON with email and postal address for the member_id.

    Execution model
    - First, attempt to call existing business logic (HANDLE_REQUEST) if present.
      This lets you plug in a real implementation without changing the agent.
    - Otherwise, return a static, deterministic structure so calling agents get
      consistent, predictable output.
    """
    # Prefer existing handler if present
    if HANDLE_REQUEST:
        try:
            _tool, payload = HANDLE_REQUEST(query=query or "", member_id=member_id)
            if isinstance(payload, (dict, list)):
                return json.dumps(payload)
            return str(payload)
        except Exception:
            pass

    resp = ProfileResponse(
        data=ProfileData(
            address=[
                NameValue(name="Address Line 1: ", value="123 Main St"),
                NameValue(name="City: ", value="Richmond"),
                NameValue(name="StateCd: ", value="VA"),
                NameValue(name="CountryCd: ", value="USA"),
                NameValue(name="ZipCd: ", value="230123"),
            ],
            email=[NameValue(name="Email: ", value=f"{member_id}@gmail.com")],
        )
    )
    return resp.model_dump_json(exclude_none=True)


PROFILE_AGENT_SYSTEM = (
    "You are the Profile Agent. When asked for email or address, "
    "call the tool get_profile_overview(member_id=...). If the user doesn't provide a member_id, "
    "ask them for it. Return ONLY the tool's JSON output."
)


# ----- Create Strands Agent and A2A server -----
profile_agent = Agent(
    name="Profile Agent",
    description="Profile overview provider (email + address) via structured JSON",
    system_prompt=PROFILE_AGENT_SYSTEM,
    tools=[get_profile_overview],
    callback_handler=None,
)

# Configure A2A server host/port so the agent card advertises the correct URL
PROFILE_AGENT_HOST = "127.0.0.1"
PROFILE_AGENT_PORT = 9003

a2a_server = A2AServer(agent=profile_agent, host=PROFILE_AGENT_HOST, port=PROFILE_AGENT_PORT)
fastapi_app = a2a_server.to_fastapi_app()


# ----- Minimal hardening: CORS + static API key for custom endpoints -----
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("PROFILE_AGENT_API_KEY", "dev-key")


@fastapi_app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Attach a correlation ID to every request/response.

    This is handy when multiple services participate in a flow and you want to
    trace a call across logs.
    """
    req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = req_id
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


@fastapi_app.middleware("http")
async def protect_custom_routes(request: Request, call_next):
    # Do NOT gate the A2A JSON-RPC root or agent-card; protect only our custom routes
    protected = ("/healthz", "/version")
    if request.url.path in protected:
        key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if not key or key != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return await call_next(request)


@fastapi_app.middleware("http")
async def timing_header(request: Request, call_next):
    """Simple timing to surface server‑side latency per request."""
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time"] = f"{(time.perf_counter() - start)*1000:.2f}ms"
    return response


# ----- Fast-path JSON-RPC handler to bypass LLM for deterministic tool -----
@fastapi_app.middleware("http")
async def fast_a2a_message_send(request: Request, call_next):
    # Intercept JSON‑RPC message/send at root and answer directly via tool.
    # This is the “fast‑path” that avoids invoking the LLM planning loop for
    # deterministic calls. The returned payload remains a valid A2A JSON‑RPC
    # response so standard SDKs accept it without special casing.
    if request.method == "POST" and request.url.path == "/":
        try:
            body = await request.body()
            payload = json.loads(body or b"{}")
            if (
                isinstance(payload, dict)
                and payload.get("jsonrpc") == "2.0"
                and payload.get("method") in ("message/send", "message.send")
            ):
                params = payload.get("params") or {}
                msg = params.get("message") or {}
                parts = msg.get("parts") or []
                text = ""
                for p in parts:
                    if isinstance(p, dict) and p.get("kind") == "text" and p.get("text"):
                        text = p["text"]
                        break

                member_id = extract_member_id(text)
                result_text = get_profile_overview(member_id=member_id, query=text)

                reply = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        # A2A requires a final Message or a Task. We return a
                        # Message with role="agent" and a unique messageId so
                        # A2A SDKs (and JSON schema) validate this response.
                        "kind": "message",
                        "role": "agent",
                        "messageId": uuid.uuid4().hex,
                        # Put the entire structured JSON string into a single
                        # text part. Downstream callers can parse this JSON or
                        # display it verbatim.
                        "parts": [{"kind": "text", "text": result_text}],
                    },
                }
                return Response(content=json.dumps(reply), media_type="application/json")
        except Exception:
            # On any parsing error, fall through to default handler
            pass
    return await call_next(request)


@fastapi_app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "profile-agent"}


@fastapi_app.get("/version")
def version():
    return {"version": "0.1.0", "agent": "Profile Agent"}


if __name__ == "__main__":
    # Serve A2A over FastAPI with our middlewares and custom endpoints
    uvicorn.run(fastapi_app, host="0.0.0.0", port=PROFILE_AGENT_PORT)
