import os
import logging
from uuid import uuid4

import uvicorn
import httpx
from strands import Agent, tool
from strands.models.anthropic import AnthropicModel
from strands_tools.a2a_client import A2AClientToolProvider
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart
load_dotenv()
from langchain_writer import ChatWriter

#EMPLOYEE_AGENT_URL = os.environ.get("EMPLOYEE_AGENT_URL", "http://localhost:8001/")

EMPLOYEE_AGENT_URL = "http://localhost:8001/"

logger = logging.getLogger(__name__)

app = FastAPI(title="HR Agent API")

class QuestionRequest(BaseModel):
    question: str


class ToolRequest(BaseModel):
    question: str
    agent_urls: Optional[List[str]] = None


class AgentToolRequest(BaseModel):
    question: str
    agent_url: Optional[str] = None
    agent_name: Optional[str] = None

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# model = AnthropicModel(
#     client_args={
#        "api_key": os.getenv("ANTHROPIC_API_KEY"),  
#     },
#     # **model_config
#     max_tokens=200,
#     model_id="claude-3-7-sonnet-20250219",
#     params={
#         "temperature": 0,
#     }
# )

from strands.models import BedrockModel
model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
    region_name="us-west-2",
    temperature=0
)

# WRITER_API_KEY = os.getenv("WRITER_API_KEY")
# model = ChatWriter(
#     model='palmyra-x5',
#     temperature=0
# )


@app.post("/query")
async def ask_agent(request: QuestionRequest):
    provider = A2AClientToolProvider(known_agent_urls=[EMPLOYEE_AGENT_URL])
    agent = Agent(model=model, tools=provider.tools)
    response = agent(request.question)
    print(f"Response: {response}")
    return response


@app.post("/inquire")
async def ask_agent(request: QuestionRequest):
    async def generate():
        provider = A2AClientToolProvider(known_agent_urls=[EMPLOYEE_AGENT_URL])

        agent = Agent(model=model, tools=provider.tools)

        stream_response = agent.stream_async(request.question)

        async for event in stream_response:
            if "data" in event:
                yield event["data"]

    return StreamingResponse(
        generate(),
        media_type="text/plain"
    )


@app.post("/client_tool")
async def client_tool(request: ToolRequest):
    """Use Strands A2A client tool provider to call other agents.

    - If `agent_urls` is provided, use those; otherwise default to EMPLOYEE_AGENT_URL.
    - Returns the agent's synchronous response to `question`.
    """
    known_urls = request.agent_urls or [EMPLOYEE_AGENT_URL]
    provider = A2AClientToolProvider(known_agent_urls=known_urls)
    agent = Agent(model=model, tools=provider.tools)
    response = agent(request.question)
    return {"response": response, "agent_urls": known_urls}


# ---- A2A Agent as a Tool ----------------------------------------------------

class A2AAgentTool:
    def __init__(self, agent_url: str, agent_name: str = "Remote Agent"):
        self.agent_url = agent_url.rstrip("/") + "/"
        self.agent_name = agent_name

    @tool
    async def call_agent(self, message: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as httpx_client:
                resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self.agent_url)
                agent_card = await resolver.get_agent_card()

                config = ClientConfig(httpx_client=httpx_client, streaming=False)
                client = ClientFactory(config).create(agent_card)

                msg = Message(
                    kind="message",
                    role=Role.user,
                    parts=[Part(TextPart(kind="text", text=message))],
                    message_id=uuid4().hex,
                )

                async for event in client.send_message(msg):
                    if isinstance(event, Message):
                        # Concatenate text parts
                        out = []
                        for p in event.parts:
                            try:
                                if hasattr(p, "text") and p.text:
                                    out.append(p.text)
                            except Exception:
                                continue
                        return "".join(out) or "<empty response>"

                    # Fallback: return JSON dump for non-Message responses
                    try:
                        return event.model_dump_json(exclude_none=True)  # type: ignore[attr-defined]
                    except Exception:
                        return str(event)

            return f"No response received from {self.agent_name}"
        except Exception as e:
            return f"Error contacting {self.agent_name}: {str(e)}"


@app.post("/client_agent_tool")
async def client_agent_tool(request: AgentToolRequest):
    """Call another A2A agent using an A2AAgentTool wrapper.

    Defaults to the Employee Agent when no URL is provided.
    """
    agent_url = (request.agent_url or EMPLOYEE_AGENT_URL).rstrip("/") + "/"
    agent_name = request.agent_name or "Employee Agent"

    remote = A2AAgentTool(agent_url=agent_url, agent_name=agent_name)
    result = await remote.call_agent(request.question)
    return {"response": result, "agent_url": agent_url, "agent_name": agent_name}

DEFAULT_TIMEOUT = 300  # 5 minutes

def create_message(*, role: Role = Role.user, text: str) -> Message:
    return Message(
        kind="message",
        role=role,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )


@app.post("/client_sync")
async def client_sync(request: QuestionRequest):
    """Synchronous A2A client call to the Employee Agent using JSON-RPC.

    Returns the first non-streaming response (Message or Task/update pair).
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as httpx_client:
        # Resolve agent card from the Employee Agent
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=EMPLOYEE_AGENT_URL)
        agent_card = await resolver.get_agent_card()

        # Create a non-streaming client
        config = ClientConfig(httpx_client=httpx_client, streaming=False)
        client = ClientFactory(config).create(agent_card)

        # Build and send message
        msg = create_message(text=request.question)

        async for event in client.send_message(msg):
            # Message response
            if isinstance(event, Message):
                payload = event.model_dump(exclude_none=True)
                logger.info("client_sync message: %s", payload)
                return payload

            # Task + UpdateEvent pair
            if isinstance(event, tuple) and len(event) == 2:
                task, update_event = event
                resp = {
                    "task": task.model_dump(exclude_none=True),
                    "update": update_event.model_dump(exclude_none=True) if update_event else None,
                }
                logger.info("client_sync task/update: %s", resp)
                return resp

            # Fallback for other response types
            try:
                payload = event.model_dump(exclude_none=True)  # type: ignore[attr-defined]
            except Exception:
                payload = {"response": str(event)}
            logger.info("client_sync other: %s", payload)
            return payload

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
