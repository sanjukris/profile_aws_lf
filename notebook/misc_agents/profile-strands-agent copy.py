from __future__ import annotations

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

    Replace this stub with real downstream calls as needed.
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
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time"] = f"{(time.perf_counter() - start)*1000:.2f}ms"
    return response


# ----- Fast-path JSON-RPC handler to bypass LLM for deterministic tool -----
@fastapi_app.middleware("http")
async def fast_a2a_message_send(request: Request, call_next):
    # Intercept JSON-RPC message/send at root and answer directly via tool
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
                        "kind": "message",
                        "role": "agent",
                        "messageId": uuid.uuid4().hex,
                        "parts": [
                            {"kind": "text", "text": result_text}
                        ],
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
