import { createGroqProvider } from "./providers/groq.ts";
import { createTavilySearchTool, createFileTools } from "./tools/index.ts";
import { Harness } from "./harness.ts";
import { startTerminalUI } from "./terminal-ui.ts";

// Automatically load .env if present
try {
  process.loadEnvFile();
} catch {
  // Ignore if .env doesn't exist
}

async function main() {
  const apiKey =
    process.env.GROQ_API_KEY ||
    process.argv.find((arg) => arg.startsWith("--api-key="))?.split("=")[1];

  if (!apiKey) {
    console.error("Error: GROQ_API_KEY is not set.");
    console.error(
      "Please set GROQ_API_KEY in your environment, in a .env file, or pass --api-key=<key>"
    );
    process.exit(1);
  }

  process.env.GROQ_API_KEY = apiKey;

  // Model selection: from CLI flag, .env GROQ_MODEL, or default to qwen/qwen3.8-27b
  const modelArg = process.argv
    .find((arg) => arg.startsWith("--model="))
    ?.split("=")[1];
  const modelId = modelArg || process.env.GROQ_MODEL || "qwen/qwen3.8-27b";

  // Temperature
  const temperature = process.env.GROQ_TEMPERATURE
    ? parseFloat(process.env.GROQ_TEMPERATURE)
    : 0.3;

  // Initialize the Harness
  const harness = new Harness();

  // Register Groq Provider
  harness.registerProvider(createGroqProvider());

  // Register Tavily Web Search Tool
  const webSearchTool = createTavilySearchTool();
  harness.registerTool(webSearchTool);

  // Register File System Tools (read, write, read_file, write_file)
  const fileTools = createFileTools();
  for (const tool of fileTools) {
    harness.registerTool(tool);
  }

  // Register Agent with web_search, read, and write tools enabled
  harness.registerAgent({
    id: "web_researcher",
    description: "An agent equipped with live web search via Tavily and file read/write capabilities",
    systemPrompt:
      "You are an autonomous AI research and execution agent.\n\n" +
      "### CORE CAPABILITIES:\n" +
      "- `web_search`: Perform real-time web search for people, entities, repositories, projects, news, facts, and live information.\n" +
      "- `read`: Read file contents at a given path (resolves from root and .artifacts/).\n" +
      "- `write`: Create or overwrite files with given content (all write outputs are automatically routed and saved under the root `.artifacts/` folder).\n\n" +
      "### 1. MANDATORY TOOL USE:\n" +
      "- Whenever the user asks about an entity, software, tool, repository, person, or recent fact, you MUST call `web_search`.\n" +
      "- NEVER reply that an entity does not exist or ask the user for clarification before attempting at least one web search.\n" +
      "- Do NOT rely on your pretraining cutoff. If a name or term is unfamiliar, immediately search the live web.\n" +
      "- Whenever the user asks to save, create, or write a file, you MUST call the `write` tool.\n\n" +
      "### 2. QUERY REWRITING & TYPO CORRECTION:\n" +
      "- Automatically correct user typos before searching (e.g. 'opeanclaw' -> 'OpenClaw', 'serch' -> 'search', 'in sort' -> 'in short').\n" +
      "- Strip conversational filler words (e.g. 'search about', 'can you find', 'tell me about').\n" +
      "- Formulate concise, targeted search queries using high-signal keywords (e.g., query 'OpenClaw creator' or 'OpenClaw game engine author' rather than 'serch about the man who build opeanclaw').\n" +
      "- If an initial search is ambiguous, execute another search with alternative keywords or spelling variations.\n\n" +
      "### 3. SEQUENTIAL WORKFLOW (SEARCH -> SYNTHESIZE -> WRITE):\n" +
      "When a user request involves researching and saving to a file:\n" +
      "- Step 1 (Search): Call `web_search` with the cleaned, rewritten query.\n" +
      "- Step 2 (Synthesize): Carefully synthesize the search results, strictly obeying user constraints (e.g., exact sentence count, bullet points, tone).\n" +
      "- Step 3 (Write): Immediately call `write` to save the synthesized report to the specified file (e.g., `openclaw_report.md` will be saved under the root `.artifacts/` directory as `.artifacts/openclaw_report.md`). Do NOT wait for another user prompt to save.\n" +
      "- Step 4 (Confirm): Deliver a clear summary and confirmation that the file was created under `.artifacts/`.",
    model: {
      providerId: "groq",
      modelId,
    },
    tools: ["web_search", "read", "write"],
  });

  // Support multi-word initial query from CLI (e.g. npm start -- "Write a summary of quantum computing...")
  const nonFlagArgs = process.argv
    .slice(2)
    .filter((arg) => !arg.startsWith("--"));
  const initialPrompt = nonFlagArgs.join(" ").trim();

  // Optional CLI arguments
  const sessionId = process.argv
    .find((arg) => arg.startsWith("--session-id="))
    ?.split("=")[1];
  const userId = process.argv
    .find((arg) => arg.startsWith("--user="))
    ?.split("=")[1];
  const userName = process.argv
    .find((arg) => arg.startsWith("--user-name="))
    ?.split("=")[1];
  const nonInteractive =
    process.argv.includes("--once") || process.argv.includes("--non-interactive");

  try {
    await startTerminalUI({
      harness,
      agentId: "web_researcher",
      apiKey,
      temperature,
      initialPrompt: initialPrompt || undefined,
      sessionId,
      userId,
      userName,
      nonInteractive,
    });
  } catch (error: any) {
    console.error("\n❌ Execution failed:", error.message || error);
    process.exit(1);
  }
}

main();
