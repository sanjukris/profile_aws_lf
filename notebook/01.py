import os
from dotenv import load_dotenv
load_dotenv()
# REQUIRED: set your AWS creds for Bedrock (or use a Colab secret)
# Option A: standard keys
os.environ.setdefault("AWS_ACCESS_KEY_ID",     "YOUR_KEY_ID")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "YOUR_SECRET")
# If you use session tokens:
# os.environ.setdefault("AWS_SESSION_TOKEN", "...")
# Option B: Bedrock bearer token (supports Bedrock 'converse' APIs in some setups)
# os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", "YOUR_BEDROCK_BEARER")

# Region with model access enabled (Claude 4.x Sonnet recommended)
os.environ.setdefault("AWS_REGION", "us-west-2")

# Optional: real profile API endpoints; if unset, code will use mock payloads
os.environ.setdefault("PROFILE_API_BASE", "")    # ex: "https://uat.api.securecloud.tbd.com"
os.environ.setdefault("PROFILE_API_KEY", "")     # ex: "tbd"
os.environ.setdefault("PROFILE_BASIC_AUTH", "")  # ex: "Basic base64(client_id:secret)"
os.environ.setdefault("DEFAULT_MEMBER_ID", "378477398")

import asyncio
import json
import logging
import threading
import time
from typing import List, Optional, Literal

import httpx
from pydantic import BaseModel, Field

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer

from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart
from uuid import uuid4

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("colab-strands")

# ----------------------------
# 1) Pydantic models
# ----------------------------

# Intent detection output model (fixed typos and aligned with rules)
class IntentEntityView(BaseModel):
    primary_intent: Literal["BENEFITS_OVERVIEW", "REVIEW_PROVIDERS", "PROFILE_OVERVIEW"]
    confidence: float = Field(ge=0.0, le=1.0)
    member_id: Optional[str] = Field(
        default=None,
        description="Include only if explicitly present in the query; otherwise null"
    )

# Final structured output schema for Profile flow
class KVPair(BaseModel):
    name: str
    value: Optional[str] = None

class UserJourney(BaseModel):
    journey: str
    subjourney: str
    task: str
    subtask: str

class Header(BaseModel):
    title: str
    description: Optional[str] = None

class EntityKV(BaseModel):
    name: str
    value: Optional[str] = None

class ProfileData(BaseModel):
    email: List[KVPair]
    address: List[KVPair]

class OrchestratorResponse(BaseModel):
    user_journey: UserJourney
    header: Header
    entities: List[EntityKV]
    data: ProfileData

# ----------------------------
# 2) System prompt for intent agent (exact spec)
# ----------------------------

SYSTEM_PROMPT_INTENT = """You are a healthcare agent. Analyze the user's query and return a structured response in the following JSON format:

{
  "primary_intent": string,
  "confidence": float (0.0 to 1.0),
  "member_id": string (optional, only include if explicitly mentioned in the query)
}

---
Intent Detection Rules:
• Set primary_intent to either 'BENEFITS_OVERVIEW', 'REVIEW_PROVIDERS', or 'PROFILE_OVERVIEW' based on the query.
• Use 'PROFILE_OVERVIEW' when the user asks about their personal information, contact details, address, or account settings.
• For profile-related queries, include the member_id if mentioned in the query; otherwise, leave it empty.

Confidence Score:
• Set confidence to a float between 0.0 and 1.0, reflecting your certainty in the intent
• Use 1.0 for clear, unambiguous queries; use lower values for ambiguous or edge cases.

Output Requirements:
• Return only the structured JSON response as specified. Do not include explanations, extra information, or any text outside the JSON.

---
Examples:
User: What are my in-network benefits for an MRI?
Response: {"primary_intent": "BENEFITS_OVERVIEW"}

User: show my contact details?
Response: {"primary_intent": "PROFILE_OVERVIEW"}
"""

# ----------------------------
# 3) Profile Tool: real HTTP if env vars set, else mock
# ----------------------------

PROFILE_API_BASE   = os.getenv("PROFILE_API_BASE", "").rstrip("/")
PROFILE_API_KEY    = os.getenv("PROFILE_API_KEY", "")
PROFILE_BASIC_AUTH = os.getenv("PROFILE_BASIC_AUTH", "")
DEFAULT_MEMBER_ID  = os.getenv("DEFAULT_MEMBER_ID", "378477398")

def _use_real_api() -> bool:
    # return bool(PROFILE_API_BASE and PROFILE_API_KEY and PROFILE_BASIC_AUTH)
    print(f"_use_real_api: False")
    return False

async def _fetch_access_token() -> Optional[str]:
    if not _use_real_api():
        # mock token
        return "MOCK_TOKEN"
    url = f"{PROFILE_API_BASE}/v1/oauth/accesstoken"
    headers = {
        "apikey": PROFILE_API_KEY,
        "Authorization": PROFILE_BASIC_AUTH,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials", "scope": "public"}
    async with httpx.AsyncClient(timeout=30) as client:
        # resp = await client.post(url, headers=headers, data=data)
        # resp.raise_for_status()
        # js = resp.json()
        # return js.get("access_token")
        print(f"_fetch_access_token: MOCK_TOKEN")
        return "MOCK_TOKEN"

async def _fetch_address(member_id: str, bearer: str) -> Optional[dict]:
    if not _use_real_api():
        print(f"_fetch_address: MOCK_ADDRESS")
        return {
            "address": [{
                "addressTypeCd": {"code": "HOME", "name": "Home", "description": "The type of address is In Home."},
                "addressLineOne": "1928288  DO NOT MAIL",
                "city": "AVON LAKE",
                "stateCd": {"code": "OH", "name": "OHIO", "description": "The State of Ohio"},
                "countryCd": {"code": "US", "name": "UNITED STATES", "description": "The country is United States"},
                "countyCd": {"code": "093", "name": "LORAIN", "description": "LORAIN"},
                "zipCd": "44012",
                "addressUid": "1733664015649003100039610"
            }]
        }
    url = f"{PROFILE_API_BASE}/genai/v1/{member_id}/address"
    headers = {"apikey": PROFILE_API_KEY, "Authorization": f"Bearer {bearer}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

async def _fetch_email(member_id: str, bearer: str) -> Optional[dict]:
    if not _use_real_api():
        print(f"_fetch_email: MOCK_EMAIL")
        return {
            "email": [{
                "emailTypeCd": {"code": "EMAIL1", "name": "EMAIL 1", "desc": "EMAIL 1"},
                "emailUid": "1750954079330009717442120",
                "emailStatusCd": {"code": "BLANK", "name": "Blank", "desc": "Status not yet received"},
                "emailAddress": "SAMPLEEMAILID_1@SAMPLEDOMAIN.COM"
            }]
        }
    url = f"{PROFILE_API_BASE}/genai/v1/{member_id}/email"
    headers = {"apikey": PROFILE_API_KEY, "Authorization": f"Bearer {bearer}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

def _shape_profile_response(member_id: str, email_json: Optional[dict], address_json: Optional[dict]) -> OrchestratorResponse:
    # Extract first records if arrays exist
    email_item = (email_json or {}).get("email", [{}])[0] if email_json else {}
    addr_item  = (address_json or {}).get("address", [{}])[0] if address_json else {}

    # Compose final structure
    data = ProfileData(
        email=[KVPair(name="Email Address: ", value=email_item.get("emailAddress"))],
        address=[
            KVPair(name="Address Type Cd", value=((addr_item.get("addressTypeCd") or {}).get("code"))),
            KVPair(name="Address Line One: ", value=addr_item.get("addressLineOne")),
            KVPair(name="Care Of: ", value=addr_item.get("careOf")),
            KVPair(name="City: ", value=addr_item.get("city")),
            KVPair(name="StateCd: ", value=((addr_item.get("stateCd") or {}).get("code"))),
            KVPair(name="CountryCd: ", value=((addr_item.get("countryCd") or {}).get("code"))),
            KVPair(name="CountyCd: ", value=((addr_item.get("countyCd") or {}).get("code"))),
            KVPair(name="ZipCd: ", value=addr_item.get("zipCd")),
            KVPair(name="ZipCdExt: ", value=addr_item.get("zipCdExt")),
        ],
    )
    entities = [
        EntityKV(name="emailUid", value=email_item.get("emailUid")),
        EntityKV(name="addressUid", value=addr_item.get("addressUid")),
    ]
    resp = OrchestratorResponse(
        user_journey=UserJourney(
            journey="MANAGE_PROFILE",
            subjourney="ENSURE_VALID_PROFILE",
            task="CHECK_PROFILE",
            subtask="PROFILE_OVERVIEW",
        ),
        header=Header(
            title=f"Your profile for {member_id}",
            description="Description Text - Optional field",
        ),
        entities=entities,
        data=data,
    )
    return resp

@tool
async def profile_tool(member_id: str) -> str:
    """
    Fetch access token, then fetch email and address for the given member_id, and return the final structured JSON string.
    Returns: JSON string that matches OrchestratorResponse schema.
    """
    token = await _fetch_access_token()
    email_json, address_json = await asyncio.gather(
        _fetch_email(member_id, token or ""),
        _fetch_address(member_id, token or ""),
    )
    shaped = _shape_profile_response(member_id, email_json, address_json)
    # Return as compact JSON for the agent to emit as-is
    return shaped.model_dump_json(exclude_none=True)

# ----------------------------
# 4) Profile Agent as A2A server
# ----------------------------

PROFILE_AGENT_SYSTEM = (
    "You are the Profile Agent. Always call the profile_tool with the provided member_id. "
    "Return only the JSON string that the tool outputs. Do not add text."
)

profile_agent = Agent(
    name="Profile Agent",
    description="Fetches member contact details via profile_tool and returns normalized JSON.",
    system_prompt=PROFILE_AGENT_SYSTEM,
    tools=[profile_tool],
)


def _run_a2a_server():
    server = A2AServer(agent=profile_agent, host="127.0.0.1", port=9000)
    server.serve()



# Start server in background thread
server_thread = threading.Thread(target=_run_a2a_server, daemon=True)
server_thread.start()
time.sleep(2)  # give server a moment to bind


# ----------------------------
# 5) Orchestrator Agent and intent detection
# ----------------------------

orchestrator_agent = Agent(
    name="Orchestrator Agent",
    system_prompt=SYSTEM_PROMPT_INTENT,
    # model=BedrockModel(model_id="anthropic.claude-sonnet-4-20250514-v1:0"),
)

async def detect_intent(user_query: str) -> IntentEntityView:
    # Pass a plain string, and use positional args per Strands examples
    result: IntentEntityView = await orchestrator_agent.structured_output_async(
        IntentEntityView,
        f"{user_query}"
    )
    return result

# ----------------------------
# 6) A2A client helper
# ----------------------------

def _create_a2a_message(text: str) -> Message:
    return Message(
        kind="message",
        role=Role.user,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )

import json, httpx
from typing import Any, Dict

async def call_profile_agent_via_a2a(member_id: str) -> Dict[str, Any]:
    base_url = "http://127.0.0.1:9000"

    # ---------- 1) Try the official client first (works when event shapes are supported) ----------
    try:
        async with httpx.AsyncClient(timeout=120) as httpx_client:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
            agent_card = await resolver.get_agent_card()

            # Try non-streaming first; some builds return a single success envelope
            config = ClientConfig(httpx_client=httpx_client, streaming=False)
            client = ClientFactory(config).create(agent_card)

            msg = _create_a2a_message(text=f"member_id={member_id}")

            final_text = None
            buffer = []

            def _extract_text_from_parts(parts):
                out = []
                for p in parts or []:
                    if isinstance(p, dict):
                        if p.get("kind") == "text" and isinstance(p.get("text"), str):
                            out.append(p["text"])
                        elif isinstance(p.get("value"), dict) and isinstance(p["value"].get("text"), str):
                            out.append(p["value"]["text"])
                    else:
                        inner = getattr(p, "value", p)
                        t = getattr(inner, "text", None) or getattr(p, "text", None)
                        if isinstance(t, str):
                            out.append(t)
                return "".join(out).strip()

            def _extract_text_from_message_obj(message):
                if isinstance(message, dict):
                    return _extract_text_from_parts(message.get("parts", []))
                return _extract_text_from_parts(getattr(message, "parts", None))

            async for event in client.send_message(msg):
                # tuple: (etype, payload)
                if isinstance(event, tuple) and len(event) >= 2:
                    payload = event[1]
                    if isinstance(payload, dict):
                        if "message" in payload:
                            t = _extract_text_from_message_obj(payload["message"])
                            if t:
                                final_text = t
                        elif isinstance(payload.get("text"), str):
                            buffer.append(payload["text"])
                        elif isinstance(payload.get("delta"), dict) and isinstance(payload["delta"].get("text"), str):
                            buffer.append(payload["delta"]["text"])
                    elif isinstance(payload, str):
                        buffer.append(payload)

                elif hasattr(event, "message"):
                    t = _extract_text_from_message_obj(event.message)
                    if t:
                        final_text = t

                elif getattr(event, "kind", "") == "message" or event.__class__.__name__ == "Message":
                    t = _extract_text_from_message_obj(event)
                    if t:
                        final_text = t

                elif hasattr(event, "delta"):
                    dt = getattr(event.delta, "text", None) or getattr(event, "textDelta", None)
                    if isinstance(dt, str):
                        buffer.append(dt)

            if not final_text and buffer:
                final_text = "".join(buffer).strip()

            if final_text:
                try:
                    return json.loads(final_text)
                except json.JSONDecodeError:
                    s, e = final_text.find("{"), final_text.rfind("}")
                    if s >= 0 and e >= 0:
                        return json.loads(final_text[s:e+1])
                    raise

    except Exception:
        # swallow and fall through to HTTP fallback
        pass

    # ---------- 2) Fallback: direct HTTP POST to the server (works across client quirks) ----------
    async with httpx.AsyncClient(timeout=120) as client:
        # Try simplest "text" body first (server advertises defaultInputModes: ["text"])
        r = await client.post(base_url + "/", json={"text": f"member_id={member_id}"})
        r.raise_for_status()
        js = r.json()

        # Common shapes to extract the response text
        text = None
        if isinstance(js, dict):
            # { "text": "...json..." }
            if isinstance(js.get("text"), str):
                text = js["text"].strip()

            # { "message": { "parts": [ { "kind": "text", "text": "...json..." } ] } }
            if not text and isinstance(js.get("message"), dict):
                parts = js["message"].get("parts") or []
                for p in parts:
                    if isinstance(p, dict) and p.get("kind") == "text" and isinstance(p.get("text"), str):
                        text = p["text"].strip()
                        break

            # { "parts": [ { "kind": "text", "text": "...json..." } ] }
            if not text and isinstance(js.get("parts"), list):
                for p in js["parts"]:
                    if isinstance(p, dict) and p.get("kind") == "text" and isinstance(p.get("text"), str):
                        text = p["text"].strip()
                        break

        if not text or not text.strip():
            # Try a more explicit message envelope if the first shot didn't work
            payload = {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "parts": [{"kind": "text", "text": f"member_id={member_id}"}],
                }
            }
            r2 = await client.post(base_url + "/", json=payload)
            r2.raise_for_status()
            js2 = r2.json()
            text = None
            if isinstance(js2, dict):
                if isinstance(js2.get("message"), dict):
                    for p in js2["message"].get("parts", []):
                        if isinstance(p, dict) and p.get("kind") == "text" and isinstance(p.get("text"), str):
                            text = p["text"].strip()
                            break
                if not text and isinstance(js2.get("text"), str):
                    text = js2["text"].strip()

        if not text:
            raise RuntimeError("Empty response from Profile Agent (HTTP fallback)")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            s, e = text.find("{"), text.rfind("}")
            if s >= 0 and e >= 0:
                return json.loads(text[s:e+1])
            raise




# ----------------------------
# 7) End-to-end demo
# ----------------------------

async def run_demo(user_query: str):
    intent = await detect_intent(user_query)
    print("Intent detection:", intent.model_dump())

    if intent.primary_intent == "PROFILE_OVERVIEW":
        member_id = intent.member_id or DEFAULT_MEMBER_ID
        result = await call_profile_agent_via_a2a(member_id)
        print("\nFinal Structured Output response:")
        print(json.dumps(result, indent=2))
    else:
        print("Primary Intent:", intent.primary_intent)

# Run the flow with your sample query
if __name__ == "__main__":
    asyncio.run(run_demo("show my contact details"))

