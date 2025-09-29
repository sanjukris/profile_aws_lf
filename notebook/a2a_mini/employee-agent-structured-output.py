import os
from typing import List
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.tools.mcp.mcp_client import MCPClient
from strands.multiagent.a2a import A2AServer
from urllib.parse import urlparse
from strands.models.anthropic import AnthropicModel
from dotenv import load_dotenv
from pydantic import BaseModel

from employee_data import EMPLOYEES
load_dotenv()

# Define URLs correctly - do not use os.environ.get() for literal values
EMPLOYEE_INFO_URL = "http://localhost:8002/mcp/"
EMPLOYEE_AGENT_URL = "http://localhost:8001/"

# Create the MCP client
employee_mcp_client = MCPClient(lambda: streamablehttp_client(EMPLOYEE_INFO_URL))


# ----- Structured Output schema and tool -----
class EmployeeMatch(BaseModel):
    name: str
    email: str
    address: str


class EmployeeMatches(BaseModel):
    matches: List[EmployeeMatch]


@tool
def get_employee_structured(name: str) -> str:
    """Return structured JSON with matching employees (name, email, address).

    Uses local data; exact name match (case-insensitive).
    """
    name_lc = (name or "").strip().lower()
    rows = [
        EmployeeMatch(name=e["name"], email=e["email"], address=e["address"])  # type: ignore[index]
        for e in EMPLOYEES
        if isinstance(e, dict) and e.get("name", "").lower() == name_lc
    ]
    result = EmployeeMatches(matches=rows)
    return result.model_dump_json(exclude_none=True)

model = AnthropicModel(
    client_args={
        "api_key": os.getenv("ANTHROPIC_API_KEY"),  # Get API key from environment variables
    },
    # **model_config
    max_tokens=1028,
    model_id="claude-3-7-sonnet-20250219",
    params={
        "temperature": 0,
    }
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
        port=int(urlparse(EMPLOYEE_AGENT_URL).port)
    )
    
    # Start the server
    if __name__ == "__main__":
        a2a_server.serve(host="0.0.0.0", port=8001)
