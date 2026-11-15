import asyncio
import os 
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from llm import generate_code


async def run_executor(session, code):
    executor_path = os.path.join(os.path.dirname(__file__), "executor.py")
    executor_code = f"""
import asyncio

async def call_tool(name, arguments):
    return await session.call_tool(name, arguments=arguments)

async def main():
    {code}

asyncio.run(main())
"""
    
    process = await asyncio.create_subprocess_exec(
        "uv", "run", "python", executor_path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:
        process.stdin.write((json.dumps({"code": code}) + "\n").encode())
        await process.stdin.drain()

        while True:

            line = await asyncio.wait_for(
                process.stdout.readline(), timeout=10
            )

            if not line:
                break

            request = json.loads(line.decode())
            print("Executor request:", request)

            result = await session.call_tool(
                request["name"],
                arguments=request["args"]
            )

            response = {
                "content": [
                    item.text for item in result.content
                    if item.type == "text"
                ],
                "is_error": result.is_error
            }

            process.stdin.write((json.dumps(response) + "\n").encode())
            await process.stdin.drain()

        await asyncio.wait_for(process.wait(), timeout=10)

        output = await process.stderr.read()
        print(output.decode())

        if process.returncode != 0:
            raise RuntimeError("Executor failed")

    finally:
        if process.returncode is None:
            process.kill()
            await process.wait() 


async def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server_path = os.path.join(project_root, "src", "mcp_server.py")

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", server_path],
        cwd=project_root
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()

            for tool in tools.tools:
                print("Available tool:", tool.name)

            prompt = input("Enter your request: ")
            code = generate_code(prompt, tools.tools)

            print("\nGenerated code:", code)

            await run_executor(session, code)


if __name__ == "__main__":
    asyncio.run(main())




