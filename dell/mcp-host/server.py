"""Small MCP endpoint that proves the Dell container path works.

Replace or extend this example with project-specific tools later. It has no
filesystem, shell, network, or model-control tool.
"""

import os

from mcp.server.fastmcp import FastMCP


mcp = FastMCP(
    "dell-mcp-demo",
    instructions="Diagnostic MCP server running in a Docker container on the Dell.",
    host=os.environ.get("MCP_BIND_HOST", "0.0.0.0"),
    port=8765,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool(description="Confirm that an MCP request reached the Dell container.")
def dell_ping(message: str = "hello") -> str:
    return f"Dell MCP is working: {message}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
