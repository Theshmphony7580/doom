# pi-in-py

A small Python agent harness inspired by Pi's separation of provider access,
agent execution, and the application. Built for reading and learning the code.

V1 supports Anthropic and Groq (through the OpenAI SDK), streamed text, sequential tools,
steering and follow-up queues, cancellation, a turn limit, and JSONL sessions.
The included tools are `list_files` and `read_file`.

## Setup

Python 3.13+ and uv are required. The project uses `.venv` in this folder.
Runtime and development dependencies are pinned; `uv.lock` locks the full environment.

```powershell
cd F:\pi_in_py
uv sync --locked
.\.venv\Scripts\Activate.ps1
```

Activation is optional when using `uv run`.

## Pi-style terminal interface

The optional terminal interface uses the published `@earendil-works/pi-tui`
package. It requires Node.js 22.19 or newer and npm, in addition to Python and uv.
From the project root, install its pinned dependencies and start it with:

```powershell
npm --prefix tui install --ignore-scripts
npm --prefix tui run start -- --demo
npm --prefix tui run start -- --provider groq --model YOUR_MODEL
```

The Node frontend owns the terminal and launches the Python agent. It displays
streamed Markdown, the session transcript, and tool activity. Enter submits;
Shift+Enter or Ctrl+J inserts a newline. Escape cancels an active run, and
Ctrl+C cancels a run or exits when idle. Type `/help` for commands, `/tools` to
expand or collapse tool output, and `/theme` to switch the light and dark
palettes. `/login groq` opens a hidden input; the key is never added to the
conversation or trace.

The launcher accepts the Python CLI's `--workspace`, `--session`, `--provider`,
`--model`, `--max-turns`, and `--trace` options. Diagnostic output, including
trace output, is printed to stderr after the TUI exits so it cannot disrupt the
screen. The original `uv run pi-in-py` command remains available for scripts,
single prompts, standalone authentication, and line-based interactive use.

This interface supports the features of pi-in-py. Pi features without a Python
backend, such as extensions, session trees, compaction, and model switching,
are not available.

## Configure API keys

The CLI loads a `.env` file from the project root. To create one without
overwriting an existing file:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Set the key and model for the provider you plan to use in `.env`:

```dotenv
ANTHROPIC_API_KEY=your-anthropic-key
ANTHROPIC_MODEL=your-anthropic-model-id
GROQ_API_KEY=your-groq-key
GROQ_MODEL=your-tool-capable-groq-model-id
```

PowerShell environment variables take precedence over values in `.env`. The
`.env` file is ignored by Git; keep real API keys there, not in `.env.example`.

## Run without an API key

```powershell
uv run pi-in-py --demo
uv run pi-in-py --demo --trace
```

The offline demo lists the workspace and reads `README.md`, then prints a scripted
answer. It demonstrates a real tool loop without calling a model. `--trace` writes
the lifecycle events, including tool results, to stderr.

## Run with a model

Anthropic is the default provider:

```powershell
uv run pi-in-py --prompt "Read README.md and pyproject.toml and explain this project."
uv run pi-in-py
```

Model choice is explicit so the example does not silently choose a paid model.
Anthropic reads its API key from `.env` or the process environment. Groq also
supports saved credentials. Process environment variables take precedence over
`.env`. A matching provider API account is required for real requests.

To use a Groq API key through the OpenAI SDK:

```powershell
uv run pi-in-py --provider groq --prompt "Read README.md and explain this project."
uv run pi-in-py --provider groq
```

Groq requests use `https://api.groq.com/openai/v1`, as documented in
[Groq's OpenAI compatibility guide](https://console.groq.com/docs/openai).
An OpenAI API key is not needed for Groq. Choose a Groq model supporting local
tool calling. `--model MODEL_ID` overrides the selected provider's model environment
variable. The provider flag determines which API key and model variables are read.

## Groq authentication

Manage saved Groq credentials without selecting a model or opening a session:

```powershell
uv run pi-in-py auth login groq
uv run pi-in-py auth status groq
uv run pi-in-py auth logout groq
```

Login uses a hidden terminal prompt and saves the API key in
`~/.pi-in-py/auth.json`, separately from conversation history. The file contains
plaintext credentials. New directories and files use private permissions on
Unix; on Windows, they inherit access control from your user profile. Hidden
input must be available; login fails rather than falling back to an echoing prompt.

For every Groq request, the AI layer resolves the key in this order:

1. An explicit key supplied to `GroqAuth` by application code.
2. The saved Groq credential.
3. `GROQ_API_KEY` from the environment, including values loaded from `.env`.

The SDK calls the resolver before each request, so an updated or removed saved
key takes effect on the next request. Login and status make no API calls and
do not prove Groq accepts a key. Logout removes the saved entry without revoking
the key at Groq; an environment key remains usable afterward.

Credential updates use `filelock` with a five-second timeout. The lock is on a
separate `auth.json.lock` file, and writes atomically replace `auth.json` after
flushing a temporary file. Updates preserve other provider entries. Corrupt
storage is reported without overwriting it or silently using an environment key.

Interactive Groq mode can open without a key, provided a model is configured.
Use `/login groq` before sending a prompt. A prompt without credentials fails
locally before any API request. Single-prompt mode still rejects missing
credentials before creating a session.

Use `--workspace PATH` to select the directory tools can read. It defaults to the
current working directory. Relative tool paths resolve inside that directory;
resolved paths outside it are rejected, including symlinks pointing outside.
File reads are UTF-8 and limited to 64 KiB; directory listings stop at 200 entries.

Interactive commands:

| Input | Behavior |
| --- | --- |
| Plain text while idle | Start a run |
| Plain text while running | Queue steering input |
| `/steer TEXT` | Affect the next turn after the current tool batch |
| `/followup TEXT` | Wait until the agent would otherwise finish |
| `/cancel` | Cancel the current run |
| `/status` | Display run state and queue sizes |
| `/login groq` | Save a Groq key using hidden input; available while idle |
| `/auth-status groq` | Display the active credential source without the key |
| `/logout groq` | Remove the saved Groq credential; available while idle |
| `/help` | Display commands |
| `/quit` | Cancel current work and exit |

While idle, `/steer TEXT` and `/followup TEXT` start a run with that text. Input is
line based: press Enter to submit a command. Output may appear while typing.
Ctrl+C exits. End-of-input waits for the active run to finish.

## Sessions

Each invocation creates a new session under `.pi-in-py/sessions/`. The path is
printed at startup. Resume by passing that path with the same workspace:

```powershell
uv run pi-in-py --session .pi-in-py\sessions\SESSION_ID.jsonl
```

The first JSONL line records the format version and workspace. Each later line
stores the messages from one completed turn, including tool calls and results.
Text deltas are display events and are not stored as separate messages.

Cancellation or a provider failure discards the unfinished turn from the in-memory
transcript. Previously completed turns remain saved. Queued input selected for an
unfinished turn is restored to its queue; pending queues are in memory only.
Resubmit a cancelled initial prompt if needed.

Corrupt or incomplete session records are rejected with a line number; v1 does
not automatically repair them. Run only one process per session file. An OS crash
during a file write can leave a partial record. The tools are read-only; recovery
of interrupted writes or shell commands is outside v1.

## Read the implementation

```text
src/pi_in_py/
  ai/
    types.py       Common messages, tool calls, stream events, Provider protocol
    adapter.py     Anthropic request/response translation and streaming
    openai.py      OpenAI-compatible Chat Completions streaming, used by Groq
    fake.py        Deterministic provider used by the demo and tests
    auth.py        Credential types and the application-owned storage protocol
    providers/groq.py  Groq login and per-request key resolution
  agent/
    types.py       Tool definitions, argument validation, lifecycle events
    loop.py        One model response plus its sequential tool batch
    agent.py       Repeated turns, queues, cancellation, and run ownership
  app/
    tools.py       Filesystem tools restricted to the selected workspace
    session.py     Append-only completed-turn storage and resume validation
    cli.py         Composition, user input, streaming output, and configuration
    auth_store.py  Locked, atomic credential persistence outside sessions
    input.py       One input thread for commands and hidden key prompts
tests/             Loop, adapter, filesystem, persistence, and CLI tests
```

Start with `ai/types.py`, then `agent/loop.py`, then `agent/agent.py`.
The agent layer imports no provider SDK. The app supplies a `Provider` and tools.

```text
user message
  -> provider streams text and returns a complete assistant message
  -> validate each requested tool's arguments
  -> execute tools in assistant order and append matching results
  -> commit the completed turn and notify session/UI listeners
  -> select one steering message, if present
  -> otherwise continue when there were tool calls
  -> otherwise select one follow-up message, if present
  -> otherwise end the run
```

A turn is one model response plus its tool executions. A run contains up to
`--max-turns` turns (default 20). A session holds the conversation across runs.
Steering does not cancel running tools; cancellation is a separate operation.
Truncated model responses never execute tools. Unknown tools, bad arguments,
execution exceptions, and tool timeouts become error results for the model.
Provider failures end the run. Tools are never automatically retried.

The `Provider` protocol yields `TextDelta` events followed by one
`ResponseCompleted` event. Implementations must close their resources when their
async generator is closed. Unexpected blocks or unsupported provider stop reasons
fail explicitly rather than silently changing conversation history. Extended
thinking, images, OAuth, dynamic tool changes, parallel tools, and compaction are
outside v1. Additional providers can implement the same protocol.

## Verify

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check src tests
uv run mypy
```

Tests use scripted providers and mocked HTTP streams through the real SDK. They
disable `.env` loading, isolate the home credential directory, clear provider
keys, and block real HTTP transports in the test process. CLI subprocess tests
inherit the isolated environment. Tests need no credentials or API tokens.
