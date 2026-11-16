"""Run generated Python in a disposable Docker container and relay MCP calls."""

import asyncio
import json
import os
import shutil
import uuid

from generated_code import build_source

IMAGE = "executor-agent-runner:local"
MAX_OUTPUT_BYTES = 65536
MAX_TOOL_CALLS = 20


def docker_prefix():
    """Find Docker locally, or use Docker in the default Windows WSL distro."""
    docker = shutil.which("docker")
    if docker:
        return [docker]
    if os.name == "nt":
        wsl = shutil.which("wsl.exe")
        if wsl:
            return [wsl, "--exec", "docker"]
    raise RuntimeError(
        "Docker is unavailable. Start Docker Desktop or install Docker in WSL."
    )


def container_name():
    """Give this request a unique container name for targeted cleanup."""
    return "executor-agent-" + uuid.uuid4().hex


async def docker_command(prefix, *arguments):
    """Run a Docker lifecycle command and stop its CLI process if cancelled."""
    process = await asyncio.create_subprocess_exec(
        *prefix,
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode != 0:
        detail = error.decode(errors="replace").strip()
        raise RuntimeError(f"Docker {arguments[0]} failed: {detail}")
    return output


async def relay_tools(process, session, allowed_tools):
    """Forward only well-formed requests for the tools discovered by the agent."""
    calls = 0
    while True:
        line = await process.stdout.readline()
        if not line:
            return
        try:
            request = json.loads(line)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError("Executor sent invalid tool-request JSON") from error
        if not isinstance(request, dict):
            raise RuntimeError("Executor tool request must be a JSON object")
        name = request.get("name")
        arguments = request.get("args")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise RuntimeError(
                "Executor tool request needs a name and an args dictionary"
            )
        if name not in allowed_tools:
            raise RuntimeError(f"Executor tool is not allowed: {name}")
        calls += 1
        if calls > MAX_TOOL_CALLS:
            raise RuntimeError(
                f"Executor exceeded the {MAX_TOOL_CALLS} tool-call limit"
            )

        result = await session.call_tool(name, arguments=arguments)
        response = {
            "content": [item.text for item in result.content if item.type == "text"],
            "is_error": result.is_error,
        }
        process.stdin.write((json.dumps(response) + "\n").encode())
        await process.stdin.drain()


async def collect_output(process, output):
    """Drain diagnostics concurrently so a full stderr pipe cannot stall a tool call."""
    while True:
        chunk = await process.stderr.read(4096)
        if not chunk:
            return
        if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
            raise RuntimeError(
                f"Executor exceeded the {MAX_OUTPUT_BYTES}-byte output limit"
            )
        output.extend(chunk)


async def run_executor(
    session, code, *, allowed_tools, timeout=30, filename="<generated>"
):
    """Execute one program with restricted Docker settings and always remove its container.

    Only JSON code and approved MCP tool responses cross into the container.
    There are no host mounts, forwarded API keys, published ports, or local-Python
    fallback. The deadline covers container creation, execution, and tool calls.
    """
    source = build_source(code)
    prefix = docker_prefix()
    name = container_name()
    process = None
    tasks = []
    output = bytearray()
    created = False

    try:
        async with asyncio.timeout(timeout):
            await docker_command(
                prefix,
                "create",
                "--name",
                name,
                "--pull",
                "never",
                "--interactive",
                "--network",
                "none",
                "--read-only",
                "--user",
                "10001:10001",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges=true",
                "--memory",
                "128m",
                "--memory-swap",
                "128m",
                "--cpus",
                "0.5",
                "--pids-limit",
                "32",
                "--ulimit",
                "nofile=128:128",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--log-driver",
                "none",
                IMAGE,
            )
            created = True
            process = await asyncio.create_subprocess_exec(
                *prefix,
                "start",
                "--attach",
                "--interactive",
                name,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_OUTPUT_BYTES,
            )
            tasks = [
                asyncio.create_task(relay_tools(process, session, set(allowed_tools))),
                asyncio.create_task(collect_output(process, output)),
            ]
            message = {"code": source, "filename": filename}
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()
            await asyncio.gather(*tasks)
            await process.wait()
            if process.returncode != 0:
                detail = output.decode(errors="replace").strip()
                raise RuntimeError(
                    f"Executor failed (exit {process.returncode}):\n{detail}"
                )
            return output.decode(errors="replace")
    except TimeoutError as error:
        raise RuntimeError(f"Executor timed out after {timeout} seconds") from error
    finally:
        cleanup_error = None
        try:
            await asyncio.wait_for(
                docker_command(prefix, "rm", "--force", name), timeout=5
            )
        except (RuntimeError, OSError, TimeoutError) as error:
            if created:
                cleanup_error = error
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if cleanup_error is not None:
            raise RuntimeError(
                f"Could not remove executor container {name}: {cleanup_error}"
            ) from cleanup_error
