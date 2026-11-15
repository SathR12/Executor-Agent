import asyncio
import json
import sys
import textwrap


async def call_tool(name: str, arguments: dict):
    request = {"name": name, "args": arguments}
    print(json.dumps(request), flush=True)

    response = await asyncio.to_thread(sys.stdin.readline)
    if not response:
        raise RuntimeError("Agent closed without sending a tool response")

    return json.loads(response)


def print_result(*values, **options):
    options["file"] = sys.stderr
    options["flush"] = True
    print(*values, **options)


async def main():
    line = await asyncio.to_thread(sys.stdin.readline)
    if not line:
        raise RuntimeError("Agent did not send any code")

    message = json.loads(line)
    code = message["code"].strip()

    # Remove Markdown fences if the model included them.
    lines = code.splitlines()
    if lines and lines[0].strip().lower() in ("```python", "```"):
        if lines[-1].strip() != "```":
            raise RuntimeError("Generated code has an unclosed Markdown fence")
        code = "\n".join(lines[1:-1]).strip()

    if not code:
        raise RuntimeError("Generated code is empty")

    # Provide functions that generated code can use.
    namespace = {
        "call_tool": call_tool,
        "print": print_result,
    }

    # Wrap the generated body in an async function.
    source = "async def generated_main():\n"
    source += textwrap.indent(code, "    ")

    # Define the function, then execute it.
    exec(compile(source, "<generated>", "exec"), namespace)
    await namespace["generated_main"]()


if __name__ == "__main__":
    asyncio.run(main())