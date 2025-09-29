# --- Colab/Local setup ---------------------------------------------------------
# If you're in Colab, uncomment these:
# !pip -q install "strands-agents[a2a]" "strands-agents-tools[a2a_client]" httpx uvicorn nest_asyncio

import os
import json
import asyncio
import logging
import threading
import time
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
load_dotenv()

# Colab-friendly event loop handling
try:
    import nest_asyncio  # type: ignore
    nest_asyncio.apply()
except Exception:
    pass

# --- Strands imports (as per docs) --------------------------------------------
# Structured output + agents
from pydantic import BaseModel, Field
from strands import Agent, tool

# A2A server + client (matching Strands docs)
from strands.multiagent.a2a import A2AServer
import asyncio, httpx, json
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart
from a2a.client.card_resolver import A2ACardResolver
import httpx

# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("profile-demo")

# ------------------------------------------------------------------------------
# Config / Env
# ------------------------------------------------------------------------------
A2A_HOST = os.getenv("A2A_HOST", "127.0.0.1")
A2A_PORT = int(os.getenv("A2A_PORT", "9000"))
A2A_BASE_URL = f"http://{A2A_HOST}:{A2A_PORT}"

# Toggle real API vs mock sample payloads
PROFILE_USE_REAL_API = os.getenv("PROFILE_USE_REAL_API", "false").lower() == "true"

# Real API settings (if you toggle on)
UAT_BASE = os.getenv("PROFILE_UAT_BASE", "https://uat.api.securecloud.tbd.com")
UAT_APIKEY = os.getenv("PROFILE_APIKEY", "tbd")
UAT_BASIC_AUTH = os.getenv("PROFILE_BASIC_AUTH", "tbd")  # base64 or 'Basic xxxxxx'

DEFAULT_MEMBER_ID = os.getenv("DEFAULT_MEMBER_ID", "378477398")


# ------------------------------------------------------------------------------
# 1) Orchestrator: Structured Output (as per docs: pass prompt string)
#    https://strandsagents.com/.../structured-output/
# ------------------------------------------------------------------------------

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
""".strip()


class IntentEntityView(BaseModel):
    """Pydantic model used by Agent.structured_output (Strands docs)."""
    primary_intent: str
    confidence: float
    # Only include if explicitly present in user query; default None
    member_id: Optional[str] = Field(
        default=None,
        description="Member ID if explicitly mentioned in the query"
    )


def new_orchestrator_agent() -> Agent:
    """
    The Bedrock provider is default in Strands; set AWS_* envs and enable model access.
    Optionally pass model id via model="anthropic.claude-sonnet-4-20250514-v1:0".
    Docs: https://strandsagents.com/.../model-providers/amazon-bedrock/
    """
    return Agent(
        name="Orchestrator Agent",
        description="Detects intent and delegates via A2A",
        system_prompt=SYSTEM_PROMPT_INTENT,
    )


async def detect_intent(user_query: str) -> IntentEntityView:
    orchestrator = new_orchestrator_agent()
    # Per docs: prompt is a string; system prompt set on agent
    result = await orchestrator.structured_output_async(
        IntentEntityView,
        f"## User Query\n{user_query}"
    )
    return result


# ------------------------------------------------------------------------------
# 2) Profile Tool and Agent (A2A Server)
#    Tool = @tool per docs: https://strandsagents.com/.../tools/python-tools/
# ------------------------------------------------------------------------------

def _mock_email_json() -> Dict[str, Any]:
    return {
        "email": [{
            "emailTypeCd": {"code": "EMAIL1", "name": "EMAIL 1", "desc": "EMAIL 1"},
            "emailUid": "1750954079330009717442120",
            "emailStatusCd": {"code": "BLANK", "name": "Blank", "desc": "Status not yet received from downstream email Marketing source"},
            "emailAddress": "SAMPLEEMAILID_1@SAMPLEDOMAIN.COM"
        }]
    }


def _mock_address_json() -> Dict[str, Any]:
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


async def _fetch_access_token_async(client: httpx.AsyncClient) -> str:
    url = f"{UAT_BASE}/v1/oauth/accesstoken"
    headers = {
        "apikey": UAT_APIKEY,
        "Authorization": f"Basic {UAT_BASIC_AUTH.replace('Basic ', '')}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials", "scope": "public"}
    resp = await client.post(url, headers=headers, data=data, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("access_token", "")


async def _fetch_email_async(client: httpx.AsyncClient, token: str, member_id: str) -> Dict[str, Any]:
    url = f"{UAT_BASE}/genai/v1/{member_id}/email"
    headers = {"apikey": UAT_APIKEY, "Authorization": f"Bearer {token}"}
    resp = await client.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


async def _fetch_address_async(client: httpx.AsyncClient, token: str, member_id: str) -> Dict[str, Any]:
    url = f"{UAT_BASE}/genai/v1/{member_id}/address"
    headers = {"apikey": UAT_APIKEY, "Authorization": f"Bearer {token}"}
    resp = await client.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _normalize_profile_payload(member_id: str, email_json: Optional[Dict[str, Any]], address_json: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    email_obj = (email_json or {}).get("email", [{}])
    email = email_obj[0] if email_obj else {}
    addr_obj = (address_json or {}).get("address", [{}])
    addr = addr_obj[0] if addr_obj else {}

    final: Dict[str, Any] = {
        "user_journey": {
            "journey": "MANAGE_PROFILE",
            "subjourney": "ENSURE_VALID_PROFILE",
            "task": "CHECK_PROFILE",
            "subtask": "PROFILE_OVERVIEW",
        },
        "header": {
            "title": f"Your profile for {member_id}",
            "description": "Description Text - Optional field",
        },
        "entities": [
            {"name": "emailUid", "value": email.get("emailUid")},
            {"name": "addressUid", "value": addr.get("addressUid")},
        ],
        "data": {
            "email": [
                {"name": "Email Address: ", "value": email.get("emailAddress")},
            ],
            "address": [
                {"name": "Address Type Cd", "value": (addr.get("addressTypeCd") or {}).get("code")},
                {"name": "Address Line One: ", "value": addr.get("addressLineOne")},
                {"name": "Care Of: ", "value": addr.get("careOf")},
                {"name": "City: ", "value": addr.get("city")},
                {"name": "StateCd: ", "value": (addr.get("stateCd") or {}).get("code")},
                {"name": "CountryCd: ", "value": (addr.get("countryCd") or {}).get("code")},
                {"name": "CountyCd: ", "value": (addr.get("countyCd") or {}).get("code")},
                {"name": "ZipCd: ", "value": addr.get("zipCd")},
                {"name": "ZipCdExt: ", "value": addr.get("zipCdExt")},
            ]
        }
    }
    return final


PROFILE_AGENT_SYSTEM = """
You are the Profile Agent. 
When you receive a message like 'member_id=<ID>', do the following:
1) Extract the member_id from the text exactly.
2) Call the tool 'profile_tool' with member_id.
3) Return EXACTLY the JSON string returned by the tool. Do not add or prepend anything.
""".strip()


@tool
async def profile_tool(member_id: str) -> str:
    """
    Fetch access token (if real API mode), then email and address for given member_id,
    normalize to a final structured JSON (string), and return it.

    Args:
        member_id: Member identifier (string)
    """
    logger.info(f"Tool #1: profile_tool (use_real_api={PROFILE_USE_REAL_API})")

    if not PROFILE_USE_REAL_API:
        email_json = _mock_email_json()
        address_json = _mock_address_json()
        final = _normalize_profile_payload(member_id, email_json, address_json)
        return json.dumps(final, separators=(",", ":"), ensure_ascii=False)

    async with httpx.AsyncClient() as client:
        token = await _fetch_access_token_async(client)
        email_json = await _fetch_email_async(client, token, member_id)
        address_json = await _fetch_address_async(client, token, member_id)
        final = _normalize_profile_payload(member_id, email_json, address_json)
        return json.dumps(final, separators=(",", ":"), ensure_ascii=False)


def make_profile_agent() -> Agent:
    return Agent(
        name="Profile Agent",
        description="Fetches member contact details via profile_tool and returns normalized JSON.",
        tools=[profile_tool],
        system_prompt=PROFILE_AGENT_SYSTEM,
        callback_handler=None,  # keep output clean
    )


def start_a2a_server_in_background(agent: Agent, host: str = A2A_HOST, port: int = A2A_PORT) -> threading.Thread:
    server = A2AServer(agent=agent, host=host, port=port)
    t = threading.Thread(target=server.serve, daemon=True)
    t.start()
    # Give uvicorn time to bind the port
    time.sleep(1.5)
    return t


# ------------------------------------------------------------------------------
# 3) A2A client (sync, as per docs)
#    https://strandsagents.com/.../agent-to-agent/  (Synchronous Client)
# ------------------------------------------------------------------------------

async def call_profile_agent(member_id: str, base_url="http://127.0.0.1:9000"):
    async with httpx.AsyncClient(timeout=300) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()
        client = ClientFactory(ClientConfig(httpx_client=httpx_client, streaming=False)).create(agent_card)

        msg = Message(kind="message", role=Role.user, parts=[Part(TextPart(kind="text", text=f"member_id={member_id}"))])

        async for event in client.send_message(msg):
            if isinstance(event, Message):
                text = "".join(
                    [(getattr(p, "text", None) or getattr(getattr(p, "value", None), "text", "")) for p in (event.parts or [])]
                ).strip()
                return json.loads(text)
            elif isinstance(event, tuple) and len(event) == 2 and getattr(event[1], "message", None):
                m = event[1].message
                text = "".join(
                    [(getattr(p, "text", None) or getattr(getattr(p, "value", None), "text", "")) for p in (m.parts or [])]
                ).strip()
                return json.loads(text)

        raise RuntimeError("No final Message received")


# ------------------------------------------------------------------------------
# 4) Orchestrate end-to-end
# ------------------------------------------------------------------------------

async def run_demo(user_query: str) -> Dict[str, Any]:
    # Intent detection
    print("\nTool #1: IntentEntityView")
    intent = await detect_intent(user_query)
    print("Intent detection:", intent.model_dump())

    if intent.primary_intent != "PROFILE_OVERVIEW":
        return {"primary_intent": intent.primary_intent, "note": "No profile lookup performed."}

    member_id = intent.member_id or DEFAULT_MEMBER_ID
    # Call Profile Agent via A2A
    result = await call_profile_agent(member_id)
    return result


# ------------------------------------------------------------------------------
# 5) Main entry (works in Colab and in plain python)
# ------------------------------------------------------------------------------

def main():
    # Start Profile Agent server
    profile_agent = make_profile_agent()
    _ = start_a2a_server_in_background(profile_agent, host=A2A_HOST, port=A2A_PORT)

    # Run demo flow
    user_query = "show my contact details"
    out = asyncio.run(run_demo(user_query))
    print("\n=== Final Structured Output ===")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
