"""Check saved source and bounded Gemini generation without making API requests."""

import ast
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import agent  # noqa: E402
import llm  # noqa: E402
from generated_code import build_source, save_generated_code  # noqa: E402


class SavedCodeTests(unittest.TestCase):
    """Ensure saved programs are inspectable and never overwrite an earlier run."""

    def test_saved_file_contains_the_complete_normalized_program(self):
        code = '```python\nresult = await call_tool("get_orders", {"status": "completed"})\nprint("café", result)\n```'
        with tempfile.TemporaryDirectory() as root:
            path = save_generated_code(code, root)
            source = Path(path).read_text(encoding="utf-8")
            self.assertEqual(source, build_source(code))
            self.assertEqual(Path(path).parent, Path(root) / "runs")
            self.assertIsInstance(ast.parse(source).body[0], ast.AsyncFunctionDef)
            self.assertIn('print("café", result)', source)
            self.assertNotIn("```", source)

    def test_two_runs_create_different_files(self):
        with tempfile.TemporaryDirectory() as root:
            first = save_generated_code('print("first")', root)
            second = save_generated_code('print("second")', root)
            self.assertNotEqual(first, second)
            self.assertIn('print("first")', Path(first).read_text(encoding="utf-8"))
            self.assertIn('print("second")', Path(second).read_text(encoding="utf-8"))

    def test_executor_traceback_includes_the_saved_filename_and_source_line(self):
        code = 'raise ValueError("example saved failure")'
        message = {"code": build_source(code), "filename": "runs/example.py"}
        result = subprocess.run(
            [sys.executable, str(ROOT / "src" / "executor.py")],
            input=json.dumps(message) + "\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("runs/example.py", result.stderr)
        self.assertIn(code, result.stderr)


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise cancellation, SDK settings, and missing credentials with fake clients."""

    def setUp(self):
        self.client = SimpleNamespace(
            models=SimpleNamespace(generate_content=AsyncMock())
        )
        self.context = MagicMock()
        self.context.__aenter__ = AsyncMock(return_value=self.client)
        self.context.__aexit__ = AsyncMock(return_value=False)
        self.provider = MagicMock(aio=self.context)
        self.factory_patch = patch.object(
            llm.genai, "Client", return_value=self.provider
        )
        self.factory = self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.env_patch = patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.dotenv_patch = patch.object(llm, "load_dotenv")
        self.dotenv_patch.start()
        self.addCleanup(self.dotenv_patch.stop)

    async def test_async_generation_has_http_timeout_and_no_hidden_retries(self):
        self.client.models.generate_content.return_value = SimpleNamespace(
            text='print("hello")'
        )
        code = await llm.generate_code("Say hello", [])
        self.assertEqual(code, 'print("hello")')
        options = self.factory.call_args.kwargs["http_options"]
        self.assertEqual(options.timeout, llm.GENERATION_TIMEOUT_SECONDS * 1000)
        self.assertEqual(options.retry_options.attempts, 1)
        self.client.models.generate_content.assert_awaited_once()
        self.context.__aexit__.assert_awaited_once()

    async def test_deadline_cancels_the_request_and_closes_the_client(self):
        cancelled = asyncio.Event()

        async def stalled_request(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.client.models.generate_content.side_effect = stalled_request
        with patch.object(llm, "GENERATION_TIMEOUT_SECONDS", 0.02):
            with self.assertRaisesRegex(RuntimeError, "Gemini generation timed out"):
                await llm.generate_code("Say hello", [])
        self.assertTrue(cancelled.is_set())
        self.context.__aexit__.assert_awaited_once()

    async def test_external_cancellation_is_not_reported_as_a_timeout(self):
        started = asyncio.Event()

        async def stalled_request(**kwargs):
            started.set()
            await asyncio.Event().wait()

        self.client.models.generate_content.side_effect = stalled_request
        task = asyncio.create_task(llm.generate_code("Say hello", []))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.context.__aexit__.assert_awaited_once()

    async def test_missing_key_fails_before_creating_a_client(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                await llm.generate_code("Say hello", [])
        self.factory.assert_not_called()

    async def test_empty_model_response_is_reported(self):
        self.client.models.generate_content.return_value = SimpleNamespace(text="   ")
        with self.assertRaisesRegex(RuntimeError, "did not return any code"):
            await llm.generate_code("Say hello", [])


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """Ensure programs are saved before execution and generation failures stop there."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        tool = SimpleNamespace(
            name="get_orders", description="Sample orders", input_schema={}
        )
        session = SimpleNamespace(
            initialize=AsyncMock(),
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[tool])),
        )
        connection = MagicMock()
        connection.__aenter__ = AsyncMock(return_value=("read", "write"))
        connection.__aexit__ = AsyncMock(return_value=False)
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        session_context.__aexit__ = AsyncMock(return_value=False)
        for target, value in (
            ("stdio_client", MagicMock(return_value=connection)),
            ("ClientSession", MagicMock(return_value=session_context)),
            ("generate_code", AsyncMock(return_value='print("hello")')),
            ("run_executor", AsyncMock(return_value="hello\n")),
            (
                "save_generated_code",
                MagicMock(
                    side_effect=lambda code, _: save_generated_code(
                        code, self.root.name
                    )
                ),
            ),
        ):
            patcher = patch.object(agent, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_agent(self):
        output = StringIO()
        errors = StringIO()
        with (
            patch("builtins.input", return_value="Say hello"),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            status = await agent.main()
        return status, output.getvalue(), errors.getvalue()

    async def test_saved_program_matches_the_program_passed_to_docker(self):
        async def execute(session, code, *, allowed_tools, filename):
            path = Path(self.root.name) / filename
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), build_source(code))
            return "hello\n"

        agent.run_executor.side_effect = execute
        status, output, errors = await self.run_agent()
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertIn("Waiting for Gemini...", output)
        self.assertIn("Saved generated code:", output)
        self.assertIn("Starting Docker executor...", output)

    async def test_executor_failure_keeps_the_saved_program(self):
        agent.run_executor.side_effect = RuntimeError("example execution failure")
        status, output, errors = await self.run_agent()
        self.assertEqual(status, 1)
        self.assertIn("example execution failure", errors)
        self.assertEqual(len(list((Path(self.root.name) / "runs").glob("*.py"))), 1)
        self.assertIn("Saved generated code:", output)

    async def test_generation_timeout_does_not_start_docker_or_save_a_program(self):
        agent.generate_code.side_effect = RuntimeError("Gemini generation timed out")
        status, output, errors = await self.run_agent()
        self.assertEqual(status, 1)
        self.assertIn("Gemini generation timed out", errors)
        agent.run_executor.assert_not_awaited()
        agent.save_generated_code.assert_not_called()


if __name__ == "__main__":
    unittest.main()
