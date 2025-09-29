from mcp.server.fastmcp import FastMCP

from employee_data import EMPLOYEES 

mcp = FastMCP("employee-server", stateless_http=True, host="0.0.0.0", port=8002)


@mcp.tool()
def get_employee_data_with_name(name: str) -> list[dict]:
    """employee data with name, email and address"""
    print(f"get_employee_data_with_name({name})")
    resp = [emp for emp in EMPLOYEES if emp["name"] == name]
    return resp 

if __name__ == "__main__":
    print(f"EMPLOYEES: {EMPLOYEES}")
    mcp.run(transport="streamable-http")