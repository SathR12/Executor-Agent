import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_path = os.path.join(os.path.dirname(__file__), "mcp_server.py")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
                print("Tool:", tool.name)
                print("Description:", tool.description)
                print("Schema:", tool.input_schema)

            result = await session.call_tool(
                "get_orders",
                arguments={"status": "completed"}
            )

            print("Result", result.content)

if __name__ == "__main__":
    asyncio.run(main())



            



    
