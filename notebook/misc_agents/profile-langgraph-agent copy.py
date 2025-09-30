from __future__ import annotations

import os
import re
import time
import uuid
import json
from typing import Any, Dict, List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

# Optional integration with your existing LangGraph handler(s)
try:
    from agents.profile_agent_lg import handle_request_async as HANDLE_REQUEST_ASYNC  # type: ignore
except Exception:
    HANDLE_REQUEST_ASYNC = None
try:
    from agents.profile_agent_lg import handle_request as HANDLE_REQUEST  # type: ignore
except Exception:
    HANDLE_REQUEST = None


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


async def run_langgraph_flow(query: str, member_id: str) -> Dict[str, Any]:
    """Run your LangGraph flow if available; else return a static stub."""
    # Prefer async handler
    if HANDLE_REQUEST_ASYNC:
        try:
            _tool, payload = await HANDLE_REQUEST_ASYNC(query=query, member_id=member_id)
            return payload if isinstance(payload, (dict, list)) else {"data": {"note": str(payload)}}
        except Exception:
            pass
    # Fallback to sync handler
    if HANDLE_REQUEST:
        try:
            _tool, payload = HANDLE_REQUEST(query=query, member_id=member_id)
            return payload if isinstance(payload, (dict, list)) else {"data": {"note": str(payload)}}
        except Exception:
            pass
    # Static fallback
    return ProfileResponse(
        data=ProfileData(
            address=[
                NameValue(name="Address Line 1: ", value="123 Main St"),
                NameValue(name="City: ", value="Anytown"),
                NameValue(name="StateCd: ", value="VA"),
                NameValue(name="CountryCd: ", value="USA"),
                NameValue(name="ZipCd: ", value="230123"),
            ],
            email=[NameValue(name="Email: ", value=f"{member_id}@gmail.com")],
        )
    ).model_dump(exclude_none=True)


# ----- FastAPI app (no Strands) -----
app = FastAPI(title="Profile LangGraph Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("PROFILE_LG_AGENT_API_KEY", "dev-key")


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = req_id
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


@app.middleware("http")
async def protect_custom_routes(request: Request, call_next):
    protected = ("/healthz", "/version")
    if request.url.path in protected:
        key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if not key or key != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return await call_next(request)


@app.middleware("http")
async def timing_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time"] = f"{(time.perf_counter() - start)*1000:.2f}ms"
    return response


# HTTP‑style (non‑JSONRPC) A2A endpoints
class TextPart(BaseModel):
    kind: str = "text"
    text: Optional[str] = None


class Message(BaseModel):
    kind: str = "message"
    role: str
    parts: List[TextPart]


def _first_text(parts: List[TextPart]) -> str:
    for p in parts or []:
        if p.kind == "text" and p.text:
            return p.text
    return ""


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "profile-langgraph-agent"}


@app.get("/version")
def version():
    return {"version": "0.1.0", "agent": "Profile LangGraph Agent"}


@app.get("/.well-known/agent-card.json")
def agent_card():
    return {
        "capabilities": {"streaming": False},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "description": "Profile overview via LangGraph; returns structured JSON",
        "name": "Profile LangGraph Agent",
        "preferredTransport": "HTTP",
        "protocolVersion": "0.3.0",
        "skills": [
            {
                "id": "get_profile_overview",
                "name": "get_profile_overview",
                "description": "Structured profile (email+address)",
                "tags": [],
            }
        ],
        "url": "http://127.0.0.1:9004/",
        "version": "0.0.1",
    }


@app.post("/a2a/messages")
async def a2a_messages(msg: Message) -> Dict[str, Any]:
    query = _first_text(msg.parts)
    member_id = extract_member_id(query)
    payload = await run_langgraph_flow(query=query, member_id=member_id)
    return {
        "kind": "message",
        "role": "assistant",
        "parts": [
            {"kind": "text", "text": "get_profile_overview"},
            {"kind": "json", "json": payload},
        ],
    }


class Query(BaseModel):
    question: str


@app.post("/query")
async def query_endpoint(body: Query) -> Dict[str, Any]:
    member_id = extract_member_id(body.question)
    payload = await run_langgraph_flow(query=body.question, member_id=member_id)
    return payload


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9004)
