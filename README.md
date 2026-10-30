# Doom

<img src="assets/branding/doom-imp.svg" alt="Doom's Twin Eye Imp logo" width="192" />

Doom is a read-only AI codebase explorer implemented in Python. It helps you
understand a project's structure, follow relevant code, and choose what to read next.

Doom is identified by the Twin Eye Imp: uneven
horns, two square eyes, and a coral cursor eye. Its terminal logo uses block
characters, so it needs no image protocol. The coral accent follows both themes;
the face switches from ivory to charcoal in the light theme.

The implementation lives in `pi_in_py`, inspired by Pi's separation of provider
access, agent execution, and the application. Built for reading and learning the code.

V1 supports Anthropic and Groq (through the OpenAI SDK), streamed text, sequential tools,
steering and follow-up queues, cancellation, a turn limit, context tracking,
automatic compaction, and JSONL sessions.
The included tools are `list_files` and `read_file`.

Doom's short, sectioned identity lives in the packaged
`src/pi_in_py/app/prompts/system.md`. The user's request determines when to explore:
greetings and general questions need no workspace tools. Explanations should
distinguish code, documentation, and inference, cite file paths and symbols,
and stop once supported. Tool descriptions carry the concrete file limits.
These model instructions and section labels do not guarantee accuracy or
protection from instructions embedded in files.

Doom explores files during the conversation; it does not yet build a persistent
code index or dependency graph, edit code, or run commands. Each application
startup loads one prompt snapshot. New and resumed chats use it for model
requests while saved conversation turns retain their original system message.

## Setup

Python 3.13+ and uv are required. The project uses `.venv` in this folder.
Runtime and development dependencies are pinned; `uv.lock` locks the full environment.

```powershell
cd F:\pi_in_py
uv sync --locked
.\.venv\Scripts\Activate.ps1
```

Activation is optional when using `uv run`.

## Doom terminal interface

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
`--model`, `--max-turns`, `--max-tool-calls`, `--context-window`, `--no-auto-compact`, and `--trace`
options. Diagnostic output, including
trace output, is printed to stderr after the TUI exits so it cannot disrupt the
screen. Use `uv run doom` for scripts,
single prompts, standalone authentication, and line-based interactive use.

This interface supports the features of Doom. Pi features without a Python
backend, such as extensions, session trees, and model switching,
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
uv run doom --demo
uv run doom --demo --trace
```

The offline demo lists the workspace and reads `README.md`, then prints a scripted
answer. It demonstrates a real tool loop without calling a model. `--trace` writes
the lifecycle events, including tool results, to stderr.

## Run with a model

Anthropic is the default provider:

```powershell
uv run doom --prompt "Read README.md and pyproject.toml and explain this project."
uv run doom
```

Model choice is explicit so the example does not silently choose a paid model.
Anthropic reads its API key from `.env` or the process environment. Groq also
supports saved credentials. Process environment variables take precedence over
`.env`. A matching provider API account is required for real requests.

To use a Groq API key through the OpenAI SDK:

```powershell
uv run doom --provider groq --prompt "Read README.md and explain this project."
uv run doom --provider groq
```

Groq requests use `https://api.groq.com/openai/v1`, as documented in
[Groq's OpenAI compatibility guide](https://console.groq.com/docs/openai).
An OpenAI API key is not needed for Groq. Choose a Groq model supporting local
tool calling. `--model MODEL_ID` overrides the selected provider's model environment
variable. The provider flag determines which API key and model variables are read.

## Groq authentication

Manage saved Groq credentials without selecting a model or opening a session:

```powershell
uv run doom auth login groq
uv run doom auth status groq
uv run doom auth logout groq
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
`read_file` reads UTF-8 text in pages. Its `offset` is a positive line number
(default 1); `limit` is a positive maximum line count (default 2,000). Every page
returns at most 2,000 complete lines or 50 KiB of file content, preserving LF/CRLF
endings. Larger requested limits still obey these caps. Absolute paths inside
the workspace also work.

For example, `read_file({"path":"src/app.py","offset":101,"limit":40})` reads
lines 101–140. When more content exists, a notice after the file text reports the
range read and the next offset. The notice is additional to the 50 KiB content
cap; no total line count is calculated. Each continuation uses another tool-call
attempt and reads the current file, so edits between calls can change the result.

Empty files return empty text at offset 1; offsets beyond EOF return errors.
A first requested line over 50 KiB returns an error naming that line; a later
oversized line ends the page before it. Use a later offset to skip such a line.
Invalid UTF-8 encountered while reading, unreadable or missing files, directories,
and paths resolving outside the workspace also return errors. Images are not
supported. Directory listings are non-recursive and stop at 200 entries; a
truncation marker means the listing is incomplete.

Each run permits 20 tool-call attempts by default; set `--max-tool-calls N` to
change the positive limit. Unknown tools, invalid arguments, execution failures,
timeouts, and cancelled attempts count. Excess calls in a batch receive matching
error results without executing. Steering and follow-ups share the run's budget;
a new run resets it. After exhaustion, tools are removed from requests and Doom
can finish from available evidence if a model turn remains. Continued tool
requests stop with `tool_limit`. The separate `--max-turns` ceiling still applies.

Interactive commands:

| Input | Behavior |
| --- | --- |
| Plain text while idle | Start a run |
| Plain text while running | Queue steering input |
| `/steer TEXT` | Affect the next turn after the current tool batch |
| `/followup TEXT` | Wait until the agent would otherwise finish |
| `/cancel` | Cancel the current run |
| `/status` | Display run state, queues, context usage, tool attempts/limit, and prompt hash |
| `/login groq` | Save a Groq key using hidden input; available while idle |
| `/auth-status groq` | Display the active credential source without the key |
| `/logout groq` | Remove the saved Groq credential; available while idle |
| `/help` | Display commands |
| `/quit` | Cancel current work and exit |

While idle, `/steer TEXT` and `/followup TEXT` start a run with that text. Input is
line based: press Enter to submit a command. Output may appear while typing.
Ctrl+C exits. End-of-input waits for the active run to finish.

## Context usage and automatic compaction

The TUI footer and `/status` show active context tokens, model capacity, and the
percentage used. The line-based CLI also reports usage after each run. `~` marks
an estimate: Doom uses the latest response's token usage plus estimates for new
messages. Before a fresh response, it estimates roughly four characters per token,
including system instructions, tool definitions, arguments, and results. These
estimates are approximate and can differ substantially for code or non-English text.
The percentage describes current context, not cumulative tokens consumed.

Model capacity comes from `--context-window`, then `ANTHROPIC_CONTEXT_WINDOW` or
`GROQ_CONTEXT_WINDOW`, then the offline catalog. For example:

```powershell
uv run doom --provider groq --model qwen/qwen3.8-27b
uv run doom --provider groq --model CUSTOM_MODEL --context-window 131072
uv run doom --no-auto-compact
```

Unknown models still show estimated tokens, but capacity is `?` and automatic
compaction is disabled until a capacity override is supplied. Catalog entries
describe capacity; they do not change which response formats an adapter supports.

Before each model request, Doom checks whether context exceeds capacity minus
16,384 reserved tokens. It summarizes older committed conversation with the
current model, retaining approximately 20,000 recent tokens and the newest complete
model/tool turn. Both budgets scale down for smaller windows. Pending user input
stays verbatim and tool calls stay paired with their results. Large summaries are
generated in bounded chunks without tools. Summary calls use the selected model's
credentials and incur normal provider usage; their text is not streamed as an answer.

Compaction appends a summary record without deleting or rewriting original turns.
The new context becomes active only after that record is saved. After compaction,
the displayed count is `?` until a new response supplies usage; Doom still uses an
internal estimate to budget requests. Resume also discards old usage anchors.
Cancellation or a failed/ineffective summary keeps the previous context. If even
the newest turn or pending prompt cannot fit, the run stops with an error; reduce
the input or use a larger model. `--no-auto-compact` disables new summaries while
retaining any saved compaction state. The scripted offline demo does not compact.

## Sessions

Each invocation creates a new session under `.pi-in-py/sessions/`. The path is
printed at startup. Resume by passing that path with the same workspace:

```powershell
uv run doom --session .pi-in-py\sessions\SESSION_ID.jsonl
```

The first JSONL line records the format version and workspace. Later lines store
completed turns, compaction summaries with retained-history boundaries, and prompt
revisions. A `prompt` record contains the exact text and its SHA-256 hash and is
appended before requests when the prompt changes. Its position identifies which
instructions apply to later turns. If recording fails, no model request is made.
Existing sessions remain resumable; their original records are not rewritten.
Resuming with changed instructions shows a notice. `/status` and `--trace` expose
the current prompt hash. Compaction and context accounting use current instructions.
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
    context.py     Provider-neutral token estimates and context usage
    models.py      Offline capacity catalog and override resolution
    auth.py        Credential types and the application-owned storage protocol
    providers/groq.py  Groq login and per-request key resolution
  agent/
    types.py       Tool definitions, argument validation, lifecycle events
    loop.py        One model response plus its sequential tool batch
    agent.py       Repeated turns, queues, cancellation, and run ownership
    compaction.py  Bounded summaries and request-context projection
  app/
    tools.py       Filesystem tools restricted to the selected workspace
    session.py     Append-only completed-turn storage and resume validation
    cli.py         Composition, user input, streaming output, and configuration
    prompt.py      Packaged prompt loading and SHA-256 identity
    prompts/system.md  Short identity, conversation, evidence, and untrusted-content rules
    branding.py    Terminal banner using the shared Twin Eye Imp sprite
    brand.json     Doom's name, palette, and terminal sprite, shared with the TUI
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
thinking, images, OAuth, runtime tool registration, and parallel tools are
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

## Opt-in model behavior evaluation

The 14 cases in `evals/cases.json` cover greetings, general questions, source-backed
answers, failures, incomplete listings, conflicting docs, injected instructions,
compacted evidence, and stopping. They run against synthetic files only. Compare
the recorded baseline identity with the packaged prompt using one model and the
same runtime settings and tool descriptions:

```powershell
.\.venv\Scripts\python.exe -m evals.run --live --provider groq --model YOUR_MODEL --output comparison.json
```

This explicitly invokes provider calls and consumes API tokens; normal tests never
run it. Use `--provider anthropic` for Anthropic, `--case greeting` to select a case
(repeatable), or `--baseline PATH` for another baseline. Credentials follow the
normal provider configuration. Choose a new output file for each comparison.

The report stores prompts, hashes, model/settings, transcripts, and tool counts.
Automatic checks require completed runs and zero tool calls for greetings and
general questions. Review each transcript against its expected behavior; semantic
results stay marked `pending` until reviewed. Scripted-provider tests check the
evaluation plumbing, not whether a live model follows instructions. A behavior
claim requires a successful, reviewed live comparison.
