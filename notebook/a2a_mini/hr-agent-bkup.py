import os

import uvicorn
from strands import Agent
from strands.models.anthropic import AnthropicModel
from strands_tools.a2a_client import A2AClientToolProvider
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
load_dotenv()
from langchain_writer import ChatWriter

#EMPLOYEE_AGENT_URL = os.environ.get("EMPLOYEE_AGENT_URL", "http://localhost:8001/")

EMPLOYEE_AGENT_URL = "http://localhost:8001/"

app = FastAPI(title="HR Agent API")

class QuestionRequest(BaseModel):
    question: str

@app.get("/health")
def health_check():
    return {"status": "healthy"}

model = AnthropicModel(
    client_args={
       "api_key": os.getenv("ANTHROPIC_API_KEY"),  
    },
    # **model_config
    max_tokens=200,
    model_id="claude-3-7-sonnet-20250219",
    params={
        "temperature": 0,
    }
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)