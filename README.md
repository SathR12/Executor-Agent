# Executor Agent

The host agent discovers MCP tools and asks Gemini to generate the body of an
async Python function. Each generated program runs in a fresh Linux Docker
container. It requests tools through JSON messages; the host calls the tools and
sends their responses back. Gemini credentials and the MCP server stay on the host.

```text
User request -> Gemini -> generated Python -> Docker executor
                                                |
                                JSON tool request / response
                                                |
                                          Host MCP session
                                                |
                                            MCP server
```

## Setup

Install uv and Python dependencies from the project root:

```powershell
uv sync
```

Create `src/.env` with your own key (the file is ignored by Git and Docker):

```text
GEMINI_API_KEY=your-key-here
```

Use Docker Desktop in Linux-container mode, or a Docker engine in your default
Ubuntu WSL distro. On Windows the runner uses `docker` if it is on PATH, otherwise
it uses `wsl.exe --exec docker`. It fails if Docker is unavailable; it never falls
back to running generated Python on the host.

Build the executor image once, and rebuild it whenever `src/executor.py` changes.
With a native Docker CLI:

```powershell
docker build -f Dockerfile.executor -t executor-agent-runner:local .
```

With the existing Ubuntu WSL Docker installation, run this from PowerShell:

```powershell
wsl.exe -d Ubuntu --cd J:\Code\Projects\Executor-Agent -- docker build -f Dockerfile.executor -t executor-agent-runner:local .
```

Only `src/executor.py` is copied into the image. No project bind mounts, `.env`
files, Docker sockets, or API keys are passed into the container. Runtime image
pulling is disabled; the build must happen in the same Docker engine the runner uses.

## Run

```powershell
uv run python src/agent.py
```

Try `Fetch completed orders` or `Find customer 2`. The tool descriptions document
the available sample data and accepted arguments. The agent handles one request,
prints its result, and removes the container. `get_customers` is an ID lookup,
not a customer-listing or city-search tool.

Progress messages show when the agent is connecting to MCP, waiting for Gemini,
saving the generated program, or starting Docker. Gemini generation has a
30-second deadline. The async SDK request is cancelled on timeout, its client is
closed, and automatic HTTP retries are disabled. No executor container starts
when generation fails. This deadline is separate from Docker's execution limit.

## Saved generated programs

Before starting Docker, the agent saves the complete wrapped Python source in
`runs/<UTC-timestamp>_<unique-id>.py` and prints its path. It strips optional
Markdown fences and wraps the generated function body in `async def generated_main()`.
The saved source is exactly what the executor compiles; generated-code tracebacks
name that file and include the failing source line. Files remain available if
Docker startup or execution fails, and existing files are never overwritten.

Open the printed `.py` file in your editor to inspect a run. It relies on runtime
functions supplied by the executor, so it is not a standalone command-line script.
Generated programs are ignored by Git and excluded from the Docker build context.
Only source text and its diagnostic filename cross the pipe; the `runs/` folder
is not mounted into the container. Saved source may contain data from your prompt,
so review it before sharing it.

The generated code must be an async function body, with no `asyncio.run()` call.
The host prepares the wrapper; the executor supplies `call_tool(name, arguments)`
and routes generated `print()` output to stderr so stdout remains a JSON
tool-request channel. The agent drains both streams while the container runs.

## Execution limits

Each container runs as UID/GID 10001 with networking disabled, a read-only root
filesystem, all Linux capabilities dropped, and privilege escalation disabled.
Limits are 128 MiB memory with no additional swap, half a CPU, 32 processes/threads,
128 file descriptors, and a 16 MiB temporary filesystem. Docker's default seccomp
profile remains enabled. These are application choices, not MCP requirements.

The host allows only discovered tool names, caps execution at 20 tool calls,
limits result/diagnostic output to 64 KiB, and gives container creation, execution,
and tool calls a combined 30-second deadline. Cleanup has an additional five-second
deadline. The container is removed on completion, execution error, timeout, or task
cancellation. Cleanup failures identify the container so it can be removed manually.

Docker adds isolation but is not a complete hostile-code security boundary.
Approved MCP tools still execute on the host with the host's permissions. Only
expose tools that the generated program is allowed to use. Prompt instructions
are guidance, not access controls.

## Tests

Fast bridge tests (no Docker engine or Gemini requests):

```powershell
uv run python -m unittest discover -s tests -v
```

After building the image, include real Docker/MCP integration checks:

```powershell
$env:RUN_DOCKER_TESTS = "1"
uv run python -m unittest discover -s tests -v
Remove-Item Env:RUN_DOCKER_TESTS
```

The real-container checks cover MCP calls and tool descriptions, host-key and
filesystem isolation, disabled networking, stderr draining, generated-code errors,
saved-file traceback names, timeout cleanup, and cancellation cleanup. The fast
checks also cover saved-source equality, unique files, and Gemini request timeout
and cancellation using fake clients. Tests use deterministic code and never call
Gemini.

## Python style

Ruff is a development dependency. Python files use four-space indentation,
double-quoted strings, grouped imports, and an 88-character formatting target.

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
```

To format edits automatically, run `uv run ruff format src tests`.
