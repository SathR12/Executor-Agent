"""Check the Docker boundary and executor bridge without calling an LLM."""

import asyncio
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import docker_executor  # noqa: E402
from generated_code import build_source, save_generated_code  # noqa: E402


def fake_process(stdout_lines=(), stderr_chunks=(), command_output=b""):
    """Make a process double with separate protocol and diagnostic streams."""
    return SimpleNamespace(
        returncode=0,
        stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=[*stdout_lines, b""])),
        stderr=SimpleNamespace(read=AsyncMock(side_effect=[*stderr_chunks, b""])),
        communicate=AsyncMock(return_value=(command_output, b"")),
        wait=AsyncMock(return_value=0),
        kill=Mock(),
    )


class DockerBridgeTests(unittest.IsolatedAsyncioTestCase):
    """Verify confinement settings, message routing, and failure cleanup."""

    async def test_tool_round_trip_uses_restricted_container_and_cleans_up(self):
        create = fake_process(command_output=b"container-id\n")
        start = fake_process(
            stdout_lines=[b'{"name":"get_orders","args":{"status":"completed"}}\n'],
            stderr_chunks=[b"order 2\n"],
        )
        remove = fake_process()
        spawn = AsyncMock(side_effect=[create, start, remove])
        session = SimpleNamespace(
            call_tool=AsyncMock(
                return_value=SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="order 2")],
                    is_error=False,
                )
            )
        )

        with (
            patch.object(docker_executor, "docker_prefix", return_value=["docker"]),
            patch.object(docker_executor.asyncio, "create_subprocess_exec", spawn),
        ):
            output = await docker_executor.run_executor(
                session, "print('test')", allowed_tools={"get_orders"}
            )

        self.assertEqual(output, "order 2\n")
        session.call_tool.assert_awaited_once_with(
            "get_orders", arguments={"status": "completed"}
        )
        messages = [
            json.loads(call.args[0]) for call in start.stdin.write.call_args_list
        ]
        self.assertEqual(
            messages[0],
            {"code": build_source("print('test')"), "filename": "<generated>"},
        )
        self.assertEqual(messages[1], {"content": ["order 2"], "is_error": False})

        create_args = spawn.call_args_list[0].args
        for flag, value in (
            ("--network", "none"),
            ("--user", "10001:10001"),
            ("--cap-drop", "ALL"),
            ("--security-opt", "no-new-privileges=true"),
            ("--memory", "128m"),
            ("--pids-limit", "32"),
            ("--pull", "never"),
        ):
            self.assertEqual(create_args[create_args.index(flag) + 1], value)
        self.assertIn("--read-only", create_args)
        self.assertNotIn("--mount", create_args)
        self.assertNotIn("--volume", create_args)
        self.assertNotIn("--env-file", create_args)
        name = create_args[create_args.index("--name") + 1]
        self.assertEqual(
            spawn.call_args_list[-1].args, ("docker", "rm", "--force", name)
        )

    async def test_unknown_tool_is_rejected_before_host_session_call(self):
        start = fake_process(stdout_lines=[b'{"name":"delete_everything","args":{}}\n'])
        spawn = AsyncMock(side_effect=[fake_process(), start, fake_process()])
        session = SimpleNamespace(call_tool=AsyncMock())
        with (
            patch.object(docker_executor, "docker_prefix", return_value=["docker"]),
            patch.object(docker_executor.asyncio, "create_subprocess_exec", spawn),
        ):
            with self.assertRaisesRegex(RuntimeError, "not allowed"):
                await docker_executor.run_executor(
                    session, "pass", allowed_tools={"get_orders"}
                )
        session.call_tool.assert_not_awaited()
        self.assertEqual(spawn.call_args_list[-1].args[1:3], ("rm", "--force"))

    async def test_missing_docker_never_executes_code_on_host(self):
        with (
            patch.object(docker_executor.shutil, "which", return_value=None),
            patch.object(
                docker_executor.asyncio,
                "create_subprocess_exec",
                new_callable=AsyncMock,
            ) as spawn,
        ):
            with self.assertRaisesRegex(RuntimeError, "Docker"):
                await docker_executor.run_executor(None, "pass", allowed_tools=set())
        spawn.assert_not_awaited()

    async def test_oversized_diagnostic_output_fails_and_removes_container(self):
        start = fake_process(
            stderr_chunks=[b"x" * (docker_executor.MAX_OUTPUT_BYTES + 1)]
        )
        spawn = AsyncMock(side_effect=[fake_process(), start, fake_process()])
        with (
            patch.object(docker_executor, "docker_prefix", return_value=["docker"]),
            patch.object(docker_executor.asyncio, "create_subprocess_exec", spawn),
        ):
            with self.assertRaisesRegex(RuntimeError, "output limit"):
                await docker_executor.run_executor(None, "pass", allowed_tools=set())
        self.assertEqual(spawn.call_args_list[-1].args[1:3], ("rm", "--force"))


@unittest.skipUnless(
    os.getenv("RUN_DOCKER_TESTS") == "1", "Set RUN_DOCKER_TESTS=1 for real containers"
)
class DockerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Run real containers with deterministic code and no Gemini requests."""

    async def assert_container_removed(self, name):
        """Confirm Docker no longer has this test's container."""
        process = await asyncio.create_subprocess_exec(
            *docker_executor.docker_prefix(),
            "inspect",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        self.assertNotEqual(process.returncode, 0)

    async def test_real_mcp_tool_call_and_tool_descriptions(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "src" / "mcp_server.py")]
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                self.assertTrue(all(tool.description for tool in tools))
                output = await docker_executor.run_executor(
                    session,
                    'result = await call_tool("get_orders", {"status": "completed"})\nprint(result)',
                    allowed_tools={tool.name for tool in tools},
                )
                self.assertIn("250", output)
                self.assertIn("completed", output)

    async def test_agent_main_connects_generated_code_to_docker_and_mcp(self):
        code = 'result = await call_tool("get_orders", {"status": "completed"})\nprint(result)'
        fake_llm = SimpleNamespace(generate_code=AsyncMock(return_value=code))
        spec = importlib.util.spec_from_file_location(
            "agent_under_test", ROOT / "src" / "agent.py"
        )
        agent = importlib.util.module_from_spec(spec)
        previous_llm = sys.modules.get("llm")
        sys.modules["llm"] = fake_llm
        try:
            spec.loader.exec_module(agent)
        finally:
            # Restore only llm. Clearing newly imported SDK modules can create
            # duplicate exception classes when AnyIO imports its backend later.
            if previous_llm is None:
                sys.modules.pop("llm", None)
            else:
                sys.modules["llm"] = previous_llm
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            with (
                patch("builtins.input", return_value="Fetch completed orders"),
                redirect_stdout(output),
                patch.object(
                    agent,
                    "save_generated_code",
                    side_effect=lambda body, _: save_generated_code(body, root),
                ),
            ):
                status = await agent.main()
        self.assertEqual(status, 0)
        self.assertIn("250", output.getvalue())
        self.assertIn("Result:", output.getvalue())
        fake_llm.generate_code.assert_awaited_once()

    async def test_container_cannot_read_host_key_write_root_or_use_network(self):
        code = """import errno
import os
import socket
assert os.getuid() != 0
assert "GEMINI_API_KEY" not in os.environ
assert not os.path.exists("/app/.env")
assert not os.path.exists("/app/src/.env")
assert not os.path.exists("/mnt/j/Code/Projects/Executor-Agent")
try:
    open("/app/should-not-exist", "w")
except OSError as error:
    assert error.errno in (errno.EROFS, errno.EACCES)
else:
    raise AssertionError("Root filesystem is writable")
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError("Network is available")
print("isolation checks passed")"""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-host-only-value"}):
            output = await docker_executor.run_executor(None, code, allowed_tools=set())
        self.assertIn("isolation checks passed", output)

    async def test_timeout_removes_running_container(self):
        name = docker_executor.container_name()
        with patch.object(docker_executor, "container_name", return_value=name):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                await docker_executor.run_executor(
                    None, "while True:\n    pass", allowed_tools=set(), timeout=3
                )
        await self.assert_container_removed(name)

    async def test_generated_exception_is_reported_and_container_removed(self):
        name = docker_executor.container_name()
        with patch.object(docker_executor, "container_name", return_value=name):
            with self.assertRaisesRegex(RuntimeError, "example generated failure"):
                await docker_executor.run_executor(
                    None,
                    'raise ValueError("example generated failure")',
                    allowed_tools=set(),
                )
        await self.assert_container_removed(name)

    async def test_generated_traceback_names_the_saved_program(self):
        code = 'raise ValueError("saved program failed")'
        with tempfile.TemporaryDirectory() as root:
            path = save_generated_code(code, root)
            filename = "runs/" + os.path.basename(path)
            with self.assertRaisesRegex(RuntimeError, "saved program failed") as caught:
                await docker_executor.run_executor(
                    None, code, allowed_tools=set(), filename=filename
                )
            self.assertIn(filename, str(caught.exception))
            self.assertIn(
                'raise ValueError("saved program failed")', str(caught.exception)
            )
            self.assertEqual(Path(path).read_text(encoding="utf-8"), build_source(code))

    async def test_diagnostic_output_before_tool_call_does_not_fill_the_pipe(self):
        session = SimpleNamespace(
            call_tool=AsyncMock(
                return_value=SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="ok")],
                    is_error=False,
                )
            )
        )
        output = await docker_executor.run_executor(
            session,
            'print("x" * 16000)\nresult = await call_tool("get_orders", {"status": "completed"})\nprint("done")',
            allowed_tools={"get_orders"},
        )
        session.call_tool.assert_awaited_once()
        self.assertTrue(output.endswith("done\n"))

    async def test_cancellation_removes_running_container(self):
        ready = asyncio.Event()

        async def signal_ready(name, arguments):
            ready.set()
            return SimpleNamespace(content=[], is_error=False)

        name = docker_executor.container_name()
        session = SimpleNamespace(call_tool=signal_ready)
        with patch.object(docker_executor, "container_name", return_value=name):
            task = asyncio.create_task(
                docker_executor.run_executor(
                    session,
                    'await call_tool("ready", {})\nwhile True:\n    pass',
                    allowed_tools={"ready"},
                )
            )
            try:
                await asyncio.wait_for(ready.wait(), timeout=30)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        await self.assert_container_removed(name)


if __name__ == "__main__":
    unittest.main()
