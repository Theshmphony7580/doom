# AGENTS.md - First_harness Codebase Context & Agent Directives

> **CRITICAL DIRECTIVE FOR ALL AI AGENTS**:
> 1. **DO NOT perform recursive codebase scans or read through multiple source files on session start.**
> 2. **ALWAYS read this `AGENTS.md` file as the single source of truth for understanding the complete codebase architecture, structure, APIs, and workflows.**
> 3. **ONLY open or read an individual source file when you are explicitly instructed to edit, test, or inspect that specific file.**
> 4. **Answer architectural and implementation questions directly from this document.**

---

## 1. Project Overview

- **Name**: `First_harness`
- **Type**: Node.js / TypeScript ES Module application (`"type": "module"`)
- **Primary Purpose**: An extensible, production-ready autonomous AI agent test harness and interactive Terminal UI (TUI). It manages LLM provider connections (Groq/OpenAI-compatible), agent tool registries (live web search via Tavily, filesystem operations), multi-turn tool calling execution loops with rate-limit retries, streaming events, and date-partitioned JSONL conversation history logging.
- **Key Dependencies**:
  - `@earendil-works/pi-ai` (^1.2.0): Model streaming, message abstractions, providers (Groq, Anthropic, OpenAI), TypeBox schema types.
  - `@earendil-works/pi-coding-agent` (^1.2.0): Tool definition helpers (`defineTool`, built-in file tools).
  - `typescript` (^7.0.2): TypeScript compiler and type checking.

---

## 2. Workspace Directory Structure

```
f:\First_harness
├── .First_harness/
│   └── conversationHistory/
│       └── brain/
│           └── YYYY-MM-DD/
│               └── sess_<timestamp>_<random>.jsonl   # Date-wise JSONL history logs
├── .agents/
│   └── rules/
│       └── codebase-rules.md                         # Workspace rule directing agents to AGENTS.md
├── src/
│   ├── history/
│   │   ├── index.ts                                  # History module entry point (exports types & logger)
│   │   ├── logger.ts                                 # SessionLogger & ConversationLogger classes
│   │   └── types.ts                                  # History records, events, user/model/tool schemas
│   ├── providers/
│   │   ├── groq.ts                                   # createGroqProvider factory
│   │   └── types.ts                                  # Groq and model stream type aliases
│   ├── tools/
│   │   ├── file.ts                                   # Filesystem tools (read, write, read_file, write_file)
│   │   ├── index.ts                                  # Tools module entry point
│   │   └── tavily.ts                                 # Tavily Web Search tool with TypeBox schema & formatting
│   ├── harness.ts                                    # Core execution engine: Harness & AgentSession classes
│   ├── index.ts                                      # Application entry point & CLI parameter parsing
│   ├── terminal-ui.ts                                # Interactive Terminal UI with ANSI colors & slash commands
│   └── types.ts                                      # Core harness configuration & agent interfaces
├── test/
│   ├── agent-session.test.ts                         # Tests for AgentSession multi-turn loop and history
│   ├── file-tools.test.ts                            # Tests for file read/write operations
│   ├── harness-history.test.ts                       # Tests for end-to-end Harness JSONL logging
│   ├── history.test.ts                               # Tests for SessionLogger & ConversationLogger
│   └── terminal-ui.test.ts                           # Tests for Terminal UI interactive and non-interactive runs
├── .env                                              # Environment variables (API keys, models)
├── .env.example                                      # Environment variable template
├── package.json                                      # Node project configuration & npm scripts
└── AGENTS.md                                         # THIS FILE - Master codebase context & agent instructions
```

---

## 3. Architecture & Core Subsystems

### A. The Core Harness (`src/harness.ts`)
1. **`Harness` Class**:
   - Central registry for AI providers (`harness.registerProvider(provider)`), tools (`harness.registerTool(tool)`), and agents (`harness.registerAgent(agent)`).
   - Holds an instance of `ConversationLogger`.
   - Resolves tools configured for an agent via `getAgentTools(agent)`. If agent specifies `tools: ["web_search", "read", "write"]`, only those tools are exposed; otherwise all tools are available.
   - `createSession(agentId, options)`: Instantiates an `AgentSession`.
   - `runAgent(agentId, prompt, options)`: Convenience one-shot execution that creates a session, runs `session.prompt(prompt)`, and closes the session.

2. **`AgentSession` Class**:
   - Coordinates multi-turn autonomous execution.
   - Maintains conversation memory in `_messages: Message[]`.
   - Resolves provider quirks: for Groq, sets `(model as any).compat.supportsDeveloperRole = false` to avoid Jinja template issues.
   - **Autonomous Tool-Calling Loop**:
     - Sends current message history to LLM stream (`harness.models.stream`).
     - Streams text deltas (`callbacks.onTextDelta`) and thinking deltas (`callbacks.onThinkingDelta`).
     - **Rate-limit resilience**: Catches 429 errors from Groq/Tavily, parses `"try again in Xs"`, waits, and automatically retries up to 3 times per turn.
     - Detects `toolCall` blocks in assistant response.
     - If tool calls exist: executes them sequentially, emits `onToolCallStart` and `onToolCallEnd`, adds `role: "toolResult"` messages, and loops back to LLM until assistant yields text without tool calls or reaches `maxTurns` (default 6).
   - **Cumulative Tracking**:
     - Aggregates input tokens, output tokens, total tokens in `_totalUsage`.
     - Records each executed tool call in `_toolCallsExecuted`.
     - Calculates session duration via `sessionDurationMs`.
   - **History Integration**: Writes every turn (user message, model response, tool call, tool result, session summary) through `SessionLogger`.

### B. History Logging System (`src/history/`)
1. **Directory Structure**:
   - Default directory: `.First_harness/conversationHistory/brain/YYYY-MM-DD/`.
   - Configurable via `HARNESS_HISTORY_DIR` env or `HarnessConfig.historyConfig.baseDir`.
   - Date folder format: `YYYY-MM-DD` (local time).
   - Session log format: `<sessionId>.jsonl` (e.g. `sess_1729000000000_abc123.jsonl`).
2. **`SessionLogger` (`src/history/logger.ts`)**:
   - Appends newline-delimited JSON (`JSONL`) sequentially using a promise chain queue (`writePromise`) to prevent file write collisions.
   - Emits events typed as `HistoryEventType`:
     - `"session_start"`
     - `"user_message"`: records prompt, role, timestamp.
     - `"model_response"`: records text, thinking, stopReason, usage, toolCalls.
     - `"tool_call"`: records tool id, name, arguments.
     - `"tool_result"`: records durationMs, isError, output content.
     - `"session_summary"`: written on `session.close("completed" | "error")` with totalUsage, duration, status.
     - `"error"`: logs unhandled exceptions.
3. **`ConversationLogger` (`src/history/logger.ts`)**:
   - Top-level manager.
   - `createSession(options)`: Spawns a new `SessionLogger`.
   - `readSessionHistory(sessionId, date?)`: Reads and parses JSONL records back into memory. If date is omitted, scans all date subdirectories.
   - `listSessions(date?)`: Lists all `.jsonl` files with sizes and paths.

### C. Tools Module (`src/tools/`)
1. **Tavily Web Search Tool (`src/tools/tavily.ts`)**:
   - Defined via `defineTool` from `@earendil-works/pi-coding-agent`.
   - Validated via `@earendil-works/pi-ai` TypeBox schemas: `TavilySearchParameters`, `TavilySearchResultItemSchema`, `TavilySearchResponseSchema`.
   - Programmatic search helper: `searchTavily(input, options)`. Queries `https://api.tavily.com/search`.
   - Result formatter: `formatTavilyResultsForLlm(data)` formats search answers, articles, snippets, and images into clean markdown.
   - Tool definition: `createTavilySearchTool(options?)` registers under name `"web_search"` with label `"Web Search (Tavily)"`.
2. **Filesystem Tools (`src/tools/file.ts`)**:
   - `resolveArtifactWritePath(path, cwd, artifactsDir)`: Sanitizes and resolves write paths so file writes are safely directed into the root `.artifacts/` folder.
   - `createReadTool(cwd, artifactsDir?)`: Standard read tool named `"read"`, resolves from `cwd` and falls back to `.artifacts/`.
   - `createWriteTool(cwd, artifactsDir?)`: Standard write tool named `"write"`, routes write operations to `.artifacts/` when configured.
   - `createFileReadTool(cwd, artifactsDir?)`: Aliased read tool named `"read_file"`.
   - `createFileWriteTool(cwd, artifactsDir?)`: Aliased write tool named `"write_file"`.
   - `createFileTools(options?)`: Helper returning all four tools, automatically routing write paths to `.artifacts/` in the workspace root by default.

### D. Terminal UI (`src/terminal-ui.ts`)
- Rich CLI interface utilizing ANSI escape styling (`c.cyan`, `c.green`, `c.bold`, etc.).
- Displays initial header banner showing active Agent, Model, Registered Tools, Session ID, and History File Path.
- Supports interactive prompt loop and single query execution (`--once` / `--non-interactive`).
- **Slash Commands**:
  - `/exit` or `exit`: Closes session, flushes summary log, and quits.
  - `/clear` or `clear`: Clears screen and re-renders banner.
  - `/new` or `/reset`: Closes current session and starts a fresh conversation session.
  - `/history`: Displays session message count, total tokens, turn count, and tool execution logs.
  - `/tools`: Lists all active tools and descriptions.
  - `/model`: Displays active agent, model provider, model ID, temperature, and maxTurns.
  - `/help`: Displays help menu.
- **Cancellation**: Intercepts `SIGINT` (Ctrl+C). If a turn is running, aborts the turn via `AbortController`; if idle, exits cleanly.

### E. Entry Point & CLI (`src/index.ts`)
- Loads environment variables using native Node.js `process.loadEnvFile()`.
- Validates `GROQ_API_KEY`.
- Registers Groq provider, `web_search` tool, and file tools (`read`, `write`, `read_file`, `write_file`).
- Registers default agent `"web_researcher"` with model `qwen/qwen3.8-27b` (or `GROQ_MODEL` / `--model=`).
- Parses CLI parameters:
  - `--api-key=<key>`: Override Groq API key.
  - `--model=<modelId>`: Override model ID.
  - `--session-id=<id>`: Custom session ID.
  - `--user=<id>`, `--user-name=<name>`: User metadata.
  - `--once` or `--non-interactive`: Run query and exit immediately.
  - Positional non-flag arguments are joined as the `initialPrompt`.

---

## 4. Key Data Models & TypeScript Interfaces

```typescript
// Agent Definition (src/types.ts)
export interface AgentDefinition {
  id: string;
  description?: string;
  systemPrompt: string;
  model: {
    providerId: string;
    modelId: string;
  };
  tools?: string[]; // List of tool names allowed for this agent
}

// Harness Configuration (src/types.ts)
export interface HarnessConfig {
  agents?: Record<string, AgentDefinition>;
  providers?: Record<string, ProviderDefinition>;
  tools?: HarnessTool[];
  historyConfig?: HarnessHistoryConfig;
}

// History Record in JSONL (src/history/types.ts)
export interface HistoryRecord {
  id: string;
  sessionId: string;
  session_id: string;
  timestamp: string;      // ISO 8601
  timestampMs: number;    // Epoch ms
  date: string;           // YYYY-MM-DD
  step: number;           // Sequential step
  turn: number;           // Agent turn
  type: HistoryEventType; // "user_message" | "model_response" | "tool_call" | "tool_result" | "session_summary" | "error"
  user: HistoryUser;
  agent: HistoryAgent;
  model: HistoryModel;
  message?: HistoryMessage | null;
  toolCall?: HistoryToolCall | null;
  output?: HistoryToolOutput | null;
  response?: HistoryModelResponse | null;
  usage?: Usage;
  durationMs?: number;
  metadata?: Record<string, unknown>;
}
```

---

## 5. Environment Variables & Configuration

- `GROQ_API_KEY`: Required. Groq Cloud API authentication key.
- `TAVILY_API_KEY`: Required for live web searching.
- `GROQ_MODEL`: Model identifier (Default: `qwen/qwen3.8-27b` or `llama-3.3-70b-versatile`).
- `GROQ_TEMPERATURE`: Sampling temperature (Default: `0.3`).
- `HARNESS_HISTORY_DIR`: Base directory for history logs (Default: `.First_harness/conversationHistory/brain`).

---

## 6. How to Run & Test

```bash
# Start interactive terminal UI
npm start

# Run with a single query and exit
npm start -- "Search for the latest news on AI" --once

# Run tests (runs Node native test runner with tsx/node loader)
npm test
```

---

## 7. Rules for Modifying Code

1. **Do not perform broad searches or read multiple files to "explore" the repo.** Refer to this `AGENTS.md` file instead.
2. Only read or edit the specific file requested by the user.
3. Preserve all existing comments and docstrings.
4. Always run `npm test` after making changes to verify that all 6 tests pass.
5. Respect TypeScript strict typing and Node.js ES module import conventions (`.ts` extensions in import paths, e.g. `import { Harness } from "./harness.ts"`).
