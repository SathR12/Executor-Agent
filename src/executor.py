"""Receive generated code inside Docker and request host tools through JSON pipes."""

import asyncio
import json
import linecache
import sys
import traceback


async def call_tool(name: str, arguments: dict):
    """Ask the host agent to call one MCP tool and wait for its JSON response."""
    request = {"name": name, "args": arguments}
    print(json.dumps(request), flush=True)

    response = await asyncio.to_thread(sys.stdin.readline)
    if not response:
        raise RuntimeError("Agent closed without sending a tool response")

    return json.loads(response)


def print_result(*values, **options):
    """Keep generated output on stderr so stdout contains only tool requests."""
    options["file"] = sys.stderr
    options["flush"] = True
    print(*values, **options)


async def main():
    """Compile the saved program received from the host and run it in this container."""
    line = await asyncio.to_thread(sys.stdin.readline)
    if not line:
        raise RuntimeError("Agent did not send any code")

    message = json.loads(line)
    source = message["code"]
    filename = message.get("filename", "<generated>")

    # Provide functions that generated code can use.
    namespace = {
        "call_tool": call_tool,
        "print": print_result,
    }

    # Include the saved filename and source lines in generated-code tracebacks.
    linecache.cache[filename] = (
        len(source),
        None,
        source.splitlines(keepends=True),
        filename,
    )
    exec(compile(source, filename, "exec"), namespace)
    await namespace["generated_main"]()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        # Python's traceback module includes source from the in-memory cache.
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1) from None
