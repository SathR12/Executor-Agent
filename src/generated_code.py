"""Prepare generated Python and save the exact source sent to the container."""

import os
import textwrap
import uuid
from datetime import datetime, timezone


def build_source(code: str) -> str:
    """Remove optional Markdown fences and wrap the body in one async function."""
    code = code.strip()
    lines = code.splitlines()
    if lines and lines[0].strip().lower() in ("```python", "```"):
        if lines[-1].strip() != "```":
            raise RuntimeError("Generated code has an unclosed Markdown fence")
        code = "\n".join(lines[1:-1]).strip()
    if not code:
        raise RuntimeError("Generated code is empty")
    return "async def generated_main():\n" + textwrap.indent(code, "    ") + "\n"


def save_generated_code(code: str, project_root: str) -> str:
    """Save an inspectable UTF-8 program before execution without overwriting a run."""
    source = build_source(code)
    runs_directory = os.path.join(project_root, "runs")
    os.makedirs(runs_directory, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%fZ")
    filename = f"{timestamp}_{uuid.uuid4().hex[:8]}.py"
    path = os.path.join(runs_directory, filename)
    with open(path, "x", encoding="utf-8", newline="\n") as saved_file:
        saved_file.write(source)
    return path
