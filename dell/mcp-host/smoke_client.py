"""Test the demo over its real Streamable HTTP transport."""

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    async with streamablehttp_client("http://127.0.0.1:8765/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert any(tool.name == "dell_ping" for tool in tools.tools), tools
            result = await session.call_tool("dell_ping", {"message": "smoke-test"})
            assert not result.isError, result
            assert any(
                getattr(block, "text", "") == "Dell MCP is working: smoke-test"
                for block in result.content
            ), result
            print("PASS: Dell MCP tool listed and called over Streamable HTTP")


if __name__ == "__main__":
    asyncio.run(main())
