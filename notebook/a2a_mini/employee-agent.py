import os
import time
import uuid
import threading
from typing import List
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.tools.mcp.mcp_client import MCPClient
from strands.multiagent.a2a import A2AServer
from urllib.parse import urlparse
from strands.models.anthropic import AnthropicModel
from dotenv import load_dotenv
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request, Response, HTTPException
from starlette import status
from pydantic import BaseModel

from employee_data import EMPLOYEES
load_dotenv()

# Define URLs correctly - do not use os.environ.get() for literal values
EMPLOYEE_INFO_URL = "http://localhost:8002/mcp/"
EMPLOYEE_AGENT_URL = "http://localhost:8001/"

# Create the MCP client
employee_mcp_client = MCPClient(lambda: streamablehttp_client(EMPLOYEE_INFO_URL))


# ----- Structured Output schema, builder, and tool -----
class EmployeeMatch(BaseModel):
    name: str
    email: str
    address: str


class EmployeeMatches(BaseModel):
    matches: List[EmployeeMatch]


def build_employee_structured(name: str) -> EmployeeMatches:
    """Pure helper that builds structured matches from local data."""
    name_lc = (name or "").strip().lower()
    rows = [
        EmployeeMatch(name=e["name"], email=e["email"], address=e["address"])  # type: ignore[index]
        for e in EMPLOYEES
        if isinstance(e, dict) and e.get("name", "").lower() == name_lc
    ]
    return EmployeeMatches(matches=rows)


@tool
def get_employee_structured(name: str) -> str:
    """Return structured JSON with matching employees (name, email, address).

    Uses local data; exact name match (case-insensitive).
    """
    result = build_employee_structured(name)
    return result.model_dump_json(exclude_none=True)

# model = AnthropicModel(
#     client_args={
#         "api_key": os.getenv("ANTHROPIC_API_KEY"),  # Get API key from environment variables
#     },
#     # **model_config
#     max_tokens=1028,
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

# Using the MCP client within a context
with employee_mcp_client:
    tools = employee_mcp_client.list_tools_sync()
    
    # Create a Strands agent
    employee_agent = Agent(
        # model=model,
        name="Employee Agent",
        description="Answers questions about employees",
        tools=[get_employee_structured] + tools,
        system_prompt=(
            "You are the Employee Agent. When asked to verify or return an employee's name, email, or address, "
            "call the tool get_employee_structured(name=...) with the provided name. Return ONLY the tool's JSON output."
        ),
    )
    
    # Create A2A server
    a2a_server = A2AServer(
        agent=employee_agent,
        host=urlparse(EMPLOYEE_AGENT_URL).hostname,
        port=int(urlparse(EMPLOYEE_AGENT_URL).port),
    )

    # Advanced customization: access FastAPI app and add middleware/routes
    app = a2a_server.to_fastapi_app()

    # CORS for local dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Request ID + Metrics + Auth Middleware ---

    API_KEY = os.getenv("EMPLOYEE_AGENT_API_KEY", "dev-key")

    class MetricsCollector:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.total_requests = 0
            self.total_errors = 0
            self.a2a_tasks_started = 0
            self.a2a_tasks_completed = 0
            self.total_duration_ms = 0.0

        def on_request(self, is_a2a: bool) -> None:
            with self.lock:
                self.total_requests += 1
                if is_a2a:
                    self.a2a_tasks_started += 1

        def on_response(self, is_a2a: bool, ok: bool, duration_ms: float) -> None:
            with self.lock:
                if not ok:
                    self.total_errors += 1
                if is_a2a and ok:
                    self.a2a_tasks_completed += 1
                self.total_duration_ms += duration_ms

        def to_dict(self) -> dict:
            with self.lock:
                avg_ms = (
                    self.total_duration_ms / self.total_requests
                    if self.total_requests > 0
                    else 0.0
                )
                return {
                    "total_requests": self.total_requests,
                    "total_errors": self.total_errors,
                    "a2a_tasks_started": self.a2a_tasks_started,
                    "a2a_tasks_completed": self.a2a_tasks_completed,
                    "avg_duration_ms": round(avg_ms, 3),
                    "total_duration_ms": round(self.total_duration_ms, 3),
                }

    metrics = MetricsCollector()

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        # Accept incoming X-Request-ID or generate a new one
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = req_id
        response: Response
        try:
            response = await call_next(request)
        finally:
            pass
        response.headers["X-Request-ID"] = req_id
        return response

    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next):
        # Protect selected custom routes; allow healthz to be public
        path = request.url.path
        protected_prefixes = ("/employee/", "/version")
        if path.startswith(protected_prefixes):
            key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
            if not key or key != API_KEY:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
        return await call_next(request)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        path = request.url.path
        method = request.method.upper()
        is_a2a = (path == "/" and method == "POST")
        metrics.on_request(is_a2a)
        try:
            response = await call_next(request)
            ok = response.status_code < 500
            return response
        except Exception:
            ok = False
            raise
        finally:
            dur_ms = (time.perf_counter() - start) * 1000.0
            metrics.on_response(is_a2a, ok, dur_ms)
            try:
                # Add duration header for convenience
                if 'response' in locals():
                    response.headers["X-Process-Time"] = f"{dur_ms:.2f}ms"
            except Exception:
                pass

    # Custom health and utility endpoints
    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "employee-agent"}

    @app.get("/version")
    def version():
        return {"version": "0.0.1", "agent": "Employee Agent"}

    @app.get("/employee/matches/{name}")
    def employee_matches(name: str):
        return build_employee_structured(name).model_dump(exclude_none=True)

    @app.get("/metrics")
    def get_metrics():
        return metrics.to_dict()

    # Serve customized app via uvicorn
    if __name__ == "__main__":
        uvicorn.run(app, host="0.0.0.0", port=8001)
