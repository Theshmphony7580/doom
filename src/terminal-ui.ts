import * as readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import type { Harness, AgentSession } from "./harness.ts";

// ANSI Styling Utilities
export const c = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  italic: "\x1b[3m",
  underline: "\x1b[4m",

  // Colors
  black: "\x1b[30m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
  white: "\x1b[37m",
  gray: "\x1b[90m",

  // Bright Colors
  brightRed: "\x1b[91m",
  brightGreen: "\x1b[92m",
  brightYellow: "\x1b[93m",
  brightBlue: "\x1b[94m",
  brightMagenta: "\x1b[95m",
  brightCyan: "\x1b[96m",
  brightWhite: "\x1b[97m",
};

export interface TerminalUIOptions {
  harness: Harness;
  agentId: string;
  apiKey: string;
  temperature?: number;
  maxTurns?: number;
  initialPrompt?: string;
  sessionId?: string;
  userId?: string;
  userName?: string;
  nonInteractive?: boolean;
  input?: NodeJS.ReadableStream;
  output?: NodeJS.WritableStream;
}

export function renderBanner(session: AgentSession): void {
  const agent = session.agent;
  const tools = session.harness.getAgentTools(agent);
  const toolNames = tools
    .map((t) => {
      if (t.name === "web_search") return "🌐 web_search (Tavily)";
      if (t.name === "read" || t.name === "read_file") return "📖 read";
      if (t.name === "write" || t.name === "write_file") return "✏️ write";
      return `🔧 ${t.name}`;
    })
    .join("  ");

  console.log(`\n${c.cyan}╭────────────────────────────────────────────────────────────────────────────╮${c.reset}`);
  console.log(`${c.cyan}│${c.reset}  ${c.bold}${c.brightWhite}⚡ FIRST HARNESS${c.reset} ${c.dim}—${c.reset} ${c.brightCyan}Autonomous Agent Terminal UI${c.reset}                       ${c.cyan}│${c.reset}`);
  console.log(`${c.cyan}│${c.reset}                                                                            ${c.cyan}│${c.reset}`);
  console.log(`${c.cyan}│${c.reset}  ${c.gray}Agent:${c.reset}    ${c.bold}${agent.id}${c.reset}`);
  console.log(`${c.cyan}│${c.reset}  ${c.gray}Model:${c.reset}    ${c.yellow}${agent.model.providerId}/${agent.model.modelId}${c.reset} ${c.dim}(temp: ${session.options.temperature ?? 0.3})${c.reset}`);
  console.log(`${c.cyan}│${c.reset}  ${c.gray}Tools:${c.reset}    ${c.green}${toolNames}${c.reset}`);
  console.log(`${c.cyan}│${c.reset}  ${c.gray}Session:${c.reset}  ${c.dim}${session.sessionId}${c.reset}`);
  if (session.historyFilePath) {
    console.log(`${c.cyan}│${c.reset}  ${c.gray}History:${c.reset}  ${c.dim}${session.historyFilePath}${c.reset}`);
  }
  console.log(`${c.cyan}│${c.reset}                                                                            ${c.cyan}│${c.reset}`);
  console.log(`${c.cyan}│${c.reset}  ${c.gray}Commands:${c.reset} ${c.bold}/exit${c.reset} ${c.dim}(quit)${c.reset}   ${c.bold}/help${c.reset} ${c.dim}(commands)${c.reset}   ${c.bold}/clear${c.reset} ${c.dim}(screen)${c.reset}   ${c.bold}/new${c.reset} ${c.dim}(reset)${c.reset} ${c.cyan}│${c.reset}`);
  console.log(`${c.cyan}╰────────────────────────────────────────────────────────────────────────────╯${c.reset}\n`);
}

function showHelp(): void {
  console.log(`\n${c.cyan}╭───────────────────── Terminal UI Commands ────────────────────╮${c.reset}`);
  console.log(`  ${c.bold}${c.brightWhite}/exit${c.reset}, ${c.bold}${c.brightWhite}exit${c.reset}         Exit the interactive terminal loop and save session`);
  console.log(`  ${c.bold}${c.brightWhite}/clear${c.reset}             Clear the screen and re-display header banner`);
  console.log(`  ${c.bold}${c.brightWhite}/new${c.reset}, ${c.bold}${c.brightWhite}/reset${c.reset}         Reset context and start a brand new conversation session`);
  console.log(`  ${c.bold}${c.brightWhite}/history${c.reset}           View conversation message counts, tokens & log path`);
  console.log(`  ${c.bold}${c.brightWhite}/tools${c.reset}             List all active tools and descriptions`);
  console.log(`  ${c.bold}${c.brightWhite}/model${c.reset}             Display current agent and model configurations`);
  console.log(`  ${c.bold}${c.brightWhite}/help${c.reset}              Show this help menu`);
  console.log(`${c.cyan}╰───────────────────────────────────────────────────────────────╯${c.reset}\n`);
}

function showTools(session: AgentSession): void {
  const tools = session.harness.getAgentTools(session.agent);
  console.log(`\n${c.cyan}╭───────────────────── Registered Tools ──────────────────────╮${c.reset}`);
  for (const t of tools) {
    console.log(`  ${c.bold}${c.green}${t.name}${c.reset}`);
    console.log(`    ${c.dim}${t.description || "No description"}${c.reset}`);
  }
  console.log(`${c.cyan}╰─────────────────────────────────────────────────────────────╯${c.reset}\n`);
}

function showModel(session: AgentSession): void {
  console.log(`\n${c.cyan}╭───────────────────── Model & Agent Info ────────────────────╮${c.reset}`);
  console.log(`  ${c.gray}Agent ID:${c.reset}       ${session.agent.id}`);
  console.log(`  ${c.gray}Description:${c.reset}    ${session.agent.description || "N/A"}`);
  console.log(`  ${c.gray}Provider:${c.reset}       ${session.agent.model.providerId}`);
  console.log(`  ${c.gray}Model ID:${c.reset}       ${session.agent.model.modelId}`);
  console.log(`  ${c.gray}Temperature:${c.reset}    ${session.options.temperature ?? 0.3}`);
  console.log(`  ${c.gray}Max Turns:${c.reset}      ${session.options.maxTurns ?? 6}`);
  console.log(`${c.cyan}╰─────────────────────────────────────────────────────────────╯${c.reset}\n`);
}

function showHistory(session: AgentSession): void {
  console.log(`\n${c.cyan}╭────────────────────── Session History ──────────────────────╮${c.reset}`);
  console.log(`  ${c.gray}Session ID:${c.reset}      ${session.sessionId}`);
  console.log(`  ${c.gray}File Location:${c.reset}   ${session.historyFilePath || "None"}`);
  console.log(`  ${c.gray}Context Count:${c.reset}   ${session.messages.length} messages in memory`);
  console.log(`  ${c.gray}Turns Executed:${c.reset}  ${session.turnCount}`);
  console.log(`  ${c.gray}Total Usage:${c.reset}     in=${session.totalUsage.input}, out=${session.totalUsage.output} (${session.totalUsage.totalTokens} tokens)`);
  console.log(`  ${c.gray}Tools Executed:${c.reset}  ${session.totalToolCalls.length}`);
  if (session.totalToolCalls.length > 0) {
    console.log(`  ${c.gray}Tool Call Log:${c.reset}`);
    for (const [idx, tc] of session.totalToolCalls.entries()) {
      const status = tc.isError ? `${c.red}failed${c.reset}` : `${c.green}ok${c.reset}`;
      console.log(`    ${c.dim}${idx + 1}.${c.reset} ${c.bold}${tc.name}${c.reset} (${status}) ${c.dim}${JSON.stringify(tc.args)}${c.reset}`);
    }
  }
  console.log(`${c.cyan}╰─────────────────────────────────────────────────────────────╯${c.reset}\n`);
}

async function handleExit(session: AgentSession, rl: readline.Interface): Promise<void> {
  console.log(`\n${c.cyan}════════════════════════════════════════════════════════════════════${c.reset}`);
  console.log(`${c.bold}${c.brightWhite}Session Closed${c.reset}`);
  console.log(`${c.gray}Session ID:    ${c.reset}${session.sessionId}`);
  if (session.historyFilePath) {
    console.log(`${c.gray}History Log:   ${c.reset}${session.historyFilePath}`);
  }
  console.log(`${c.gray}Total Turns:   ${c.reset}${session.turnCount}`);
  console.log(`${c.gray}Total Tokens:  ${c.reset}in=${session.totalUsage.input}, out=${session.totalUsage.output} (total: ${session.totalUsage.totalTokens})`);
  console.log(`${c.gray}Tools Called:  ${c.reset}${session.totalToolCalls.length}`);
  console.log(`${c.gray}Total Time:    ${c.reset}${session.sessionDurationMs}ms`);
  console.log(`${c.cyan}════════════════════════════════════════════════════════════════════${c.reset}`);
  console.log(`${c.green}👋 Goodbye!${c.reset}\n`);

  await session.close("completed");
  rl.close();
}

export async function executeTurn(
  session: AgentSession,
  prompt: string,
  turnOptions?: { signal?: AbortSignal }
): Promise<void> {
  console.log(`\n${c.cyan}🤖 [${session.agent.id}] Executing...${c.reset}`);

  let hasShownResponseHeader = false;

  try {
    const result = await session.prompt(prompt, {
      signal: turnOptions?.signal,
      callbacks: {
        onToolCallStart(toolName, args) {
          console.log(`\n${c.yellow}⚙️  [Calling Tool: ${c.bold}${toolName}${c.reset}${c.yellow}]${c.reset} ${c.dim}args: ${JSON.stringify(args)}${c.reset}`);
        },
        onToolCallEnd(toolName, summary) {
          console.log(`${c.green}✅ [Tool Completed: ${c.bold}${toolName}${c.reset}${c.green}]${c.reset} ${summary}`);
        },
        onThinkingDelta(delta) {
          process.stdout.write(`${c.dim}${delta}${c.reset}`);
        },
        onTextDelta(delta) {
          if (!hasShownResponseHeader) {
            console.log(`\n${c.cyan}--- Response ---${c.reset}\n`);
            hasShownResponseHeader = true;
          }
          process.stdout.write(delta);
        },
      },
    });

    console.log(`\n`);
    console.log(`${c.gray}────────────────────────────────────────────────────────────────────────────${c.reset}`);
    console.log(
      `${c.dim}⏱️  Duration:${c.reset} ${c.bold}${result.durationMs}ms${c.reset}  │  ` +
      `${c.dim}🪙 Tokens:${c.reset} in=${result.totalUsage.input}, out=${result.totalUsage.output} (total: ${result.totalUsage.totalTokens})  │  ` +
      `${c.dim}🛠️  Tools:${c.reset} ${result.toolCallsExecuted.length}`
    );
    console.log(`${c.gray}────────────────────────────────────────────────────────────────────────────${c.reset}\n`);
  } catch (err: any) {
    if (err.name === "AbortError" || turnOptions?.signal?.aborted) {
      console.log(`\n${c.yellow}⚠️  Turn cancelled by user.${c.reset}\n`);
    } else {
      console.error(`\n${c.red}❌ Error executing turn:${c.reset}`, err.message || err);
      console.log(`${c.dim}You can continue your conversation or try rephrasing.${c.reset}\n`);
    }
  }
}

/**
 * Main Terminal UI Entry Point
 */
export async function startTerminalUI(options: TerminalUIOptions): Promise<void> {
  const {
    harness,
    agentId,
    apiKey,
    temperature,
    maxTurns,
    initialPrompt,
    sessionId,
    userId,
    userName,
    nonInteractive,
  } = options;

  let session = harness.createSession(agentId, {
    apiKey,
    temperature,
    maxTurns,
    sessionId,
    userId,
    userName,
  });

  renderBanner(session);

  const rl = readline.createInterface({
    input: options.input || input,
    output: options.output || output,
  });

  let activeAbortController: AbortController | null = null;
  let isExiting = false;

  const handleInterrupt = async () => {
    if (activeAbortController) {
      console.log(`\n${c.yellow}⚠️  Aborting current execution...${c.reset}`);
      activeAbortController.abort();
      activeAbortController = null;
    } else {
      if (isExiting) return;
      isExiting = true;
      await handleExit(session, rl);
      if (!options.input) {
        process.exit(0);
      }
    }
  };

  process.on("SIGINT", handleInterrupt);

  // If an initial prompt was provided on CLI, execute it first
  if (initialPrompt && initialPrompt.trim().length > 0) {
    console.log(`${c.cyan}╭─ ${c.brightWhite}${c.bold}Initial Query${c.reset}`);
    console.log(`${c.cyan}╰─❯ ${c.reset}${initialPrompt}\n`);

    activeAbortController = new AbortController();
    await executeTurn(session, initialPrompt.trim(), { signal: activeAbortController.signal });
    activeAbortController = null;

    if (nonInteractive) {
      isExiting = true;
      await handleExit(session, rl);
      return;
    }
  }

  // Interactive Prompt Loop
  const promptText = `${c.cyan}╭─ ${c.green}${c.bold}you${c.reset}${c.cyan}\n╰─❯ ${c.reset}`;

  while (!isExiting) {
    let inputLine: string;
    try {
      inputLine = await rl.question(promptText);
    } catch {
      // Stream closed or EOF
      break;
    }

    const trimmed = inputLine.trim();
    if (!trimmed) {
      continue;
    }

    const lower = trimmed.toLowerCase();

    // Check Slash Commands
    if (lower === "/exit" || lower === "exit" || lower === "/quit" || lower === "quit") {
      isExiting = true;
      await handleExit(session, rl);
      if (!options.input) {
        process.exit(0);
      }
      break;
    }

    if (lower === "/help" || lower === "help") {
      showHelp();
      continue;
    }

    if (lower === "/clear" || lower === "clear") {
      console.clear();
      renderBanner(session);
      continue;
    }

    if (lower === "/new" || lower === "/reset") {
      await session.close("completed");
      session = harness.createSession(agentId, {
        apiKey,
        temperature,
        maxTurns,
        userId,
        userName,
      });
      console.log(`\n${c.green}✨ Fresh conversation session started:${c.reset} ${c.bold}${session.sessionId}${c.reset}`);
      if (session.historyFilePath) {
        console.log(`${c.dim}History: ${session.historyFilePath}${c.reset}\n`);
      }
      continue;
    }

    if (lower === "/history") {
      showHistory(session);
      continue;
    }

    if (lower === "/tools") {
      showTools(session);
      continue;
    }

    if (lower === "/model") {
      showModel(session);
      continue;
    }

    // Execute standard user query turn
    activeAbortController = new AbortController();
    await executeTurn(session, trimmed, { signal: activeAbortController.signal });
    activeAbortController = null;
  }

  if (!isExiting) {
    isExiting = true;
    await handleExit(session, rl);
    if (!options.input) {
      process.exit(0);
    }
  }
}
