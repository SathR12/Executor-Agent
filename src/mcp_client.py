"""Demonstrate MCP discovery and a direct sample tool call without using Gemini."""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    """Start the sample server, describe its tools, and fetch completed orders."""
    server_path = os.path.join(os.path.dirname(__file__), "mcp_server.py")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    server_params = StdioServerParameters(
        command=sys.executable, args=[server_path], cwd=project_root
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()

            for tool in tools.tools:
                print("Tool:", tool.name)
                print("Description:", tool.description)
                print("Schema:", tool.input_schema)

            result = await session.call_tool(
                "get_orders", arguments={"status": "completed"}
            )

            print("Result", result.content)


if __name__ == "__main__":
    asyncio.run(main())
