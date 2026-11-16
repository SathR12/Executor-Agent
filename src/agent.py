"""Discover MCP tools, generate Python, and execute it through the Docker bridge."""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from docker_executor import run_executor
from generated_code import save_generated_code
from llm import generate_code


async def main() -> int:
    """Handle one user request while keeping Gemini and MCP on the host."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server_path = os.path.join(project_root, "src", "mcp_server.py")

    server_params = StdioServerParameters(
        command=sys.executable, args=[server_path], cwd=project_root
    )

    print("Connecting to MCP server...", flush=True)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()

            for tool in tools.tools:
                print("Available tool:", tool.name)

            try:
                prompt = await asyncio.to_thread(input, "Enter your request: ")
                print("Waiting for Gemini...", flush=True)
                code = await generate_code(prompt, tools.tools)
                print("\nGenerated code:", code)
                saved_path = save_generated_code(code, project_root)
                print(f"Saved generated code: {saved_path}", flush=True)
                print("Starting Docker executor...", flush=True)
                output = await run_executor(
                    session,
                    code,
                    allowed_tools={tool.name for tool in tools.tools},
                    filename="runs/" + os.path.basename(saved_path),
                )
                print("\nResult:\n" + output, end="" if output.endswith("\n") else "\n")
            except Exception as error:
                print(f"Agent failed: {error}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
