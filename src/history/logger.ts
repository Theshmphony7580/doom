import * as fs from "node:fs/promises";
import * as path from "node:path";
import type {
  CreateSessionOptions,
  HarnessHistoryConfig,
  HistoryAgent,
  HistoryModel,
  HistoryModelResponse,
  HistoryRecord,
  HistoryToolCall,
  HistoryToolOutput,
  HistoryUser,
  SessionSummary,
} from "./types.ts";
import type { AssistantMessage, Message } from "@earendil-works/pi-ai";

/**
 * Returns the current date in YYYY-MM-DD format based on local time.
 */
export function getLocalDateString(d: Date = new Date()): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Generate a unique session ID
 */
export function generateSessionId(): string {
  const timestamp = Date.now();
  const random = Math.random().toString(36).substring(2, 8);
  return `sess_${timestamp}_${random}`;
}

/**
 * SessionLogger: Manages sequential JSONL writing for a single conversation session.
 */
export class SessionLogger {
  readonly sessionId: string;
  readonly date: string;
  readonly filePath: string;
  readonly sessionDir: string;
  readonly user: HistoryUser;
  readonly agent: HistoryAgent;
  readonly model: HistoryModel;
  readonly metadata?: Record<string, unknown>;

  private step = 0;
  private currentTurn = 0;
  private writePromise: Promise<void> = Promise.resolve();
  private dirInitialized = false;

  constructor(
    baseDir: string,
    options: CreateSessionOptions
  ) {
    this.date = options.date || getLocalDateString();
    this.sessionId = options.sessionId || generateSessionId();
    this.user = options.user || { id: "default_user", name: "User" };
    this.agent = options.agent;
    this.model = options.model;
    this.metadata = options.metadata;

    this.sessionDir = path.resolve(baseDir, this.date);
    this.filePath = path.join(this.sessionDir, `${this.sessionId}.jsonl`);
  }

  private async ensureDir(): Promise<void> {
    if (!this.dirInitialized) {
      await fs.mkdir(this.sessionDir, { recursive: true });
      this.dirInitialized = true;
    }
  }

  /**
   * Enqueues a record to be safely appended to the JSONL file.
   */
  private appendRecord(record: HistoryRecord): Promise<HistoryRecord> {
    const serialized = JSON.stringify(record) + "\n";
    this.writePromise = this.writePromise
      .then(async () => {
        await this.ensureDir();
        await fs.appendFile(this.filePath, serialized, "utf-8");
      })
      .catch((err) => {
        console.error(`[SessionLogger] Failed to write record to ${this.filePath}:`, err);
      });

    return this.writePromise.then(() => record);
  }

  private createBaseRecord(type: HistoryRecord["type"], turn: number): HistoryRecord {
    this.step++;
    const now = new Date();
    return {
      id: `rec_${this.sessionId}_${this.step}`,
      sessionId: this.sessionId,
      session_id: this.sessionId,
      timestamp: now.toISOString(),
      timestampMs: now.getTime(),
      date: this.date,
      step: this.step,
      turn,
      type,
      user: this.user,
      agent: this.agent,
      model: this.model,
      message: null,
      toolCall: null,
      tool_call: null,
      output: null,
      response: null,
      metadata: this.metadata,
    };
  }

  /**
   * Log a user message/prompt that kicked off a turn in the run
   */
  async logUserMessage(prompt: string, message: Message, turn?: number): Promise<HistoryRecord> {
    this.currentTurn = turn ?? (this.currentTurn === 0 ? 1 : this.currentTurn + 1);
    const record: HistoryRecord = {
      ...this.createBaseRecord("user_message", this.currentTurn),
      message: {
        role: message.role,
        content: message.content,
        timestamp: message.timestamp,
      },
      response: null,
      toolCall: null,
      tool_call: null,
      output: null,
    };

    return this.appendRecord(record);
  }

  /**
   * Log a model response turn (text, thinking, tool calls, token usage)
   */
  async logModelResponse(
    response: HistoryModelResponse,
    message: AssistantMessage,
    turn: number
  ): Promise<HistoryRecord> {
    this.currentTurn = turn;

    const toolCalls: HistoryToolCall[] = message.content
      .filter((part): part is Extract<typeof part, { type: "toolCall" }> => part.type === "toolCall")
      .map((part) => ({
        id: part.id,
        name: part.name,
        args: part.arguments,
      }));

    const record: HistoryRecord = {
      ...this.createBaseRecord("model_response", turn),
      message: {
        role: message.role,
        content: message.content,
        timestamp: message.timestamp,
      },
      response,
      toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      toolCall: toolCalls.length > 0 ? toolCalls[0] : null,
      tool_call: toolCalls.length > 0 ? toolCalls[0] : null,
      output: null,
      usage: response.usage,
    };

    return this.appendRecord(record);
  }

  /**
   * Log an individual tool call initiated by the model
   */
  async logToolCall(toolCall: HistoryToolCall, turn: number): Promise<HistoryRecord> {
    const record: HistoryRecord = {
      ...this.createBaseRecord("tool_call", turn),
      toolCall,
      tool_call: toolCall,
      output: null,
      response: null,
    };

    return this.appendRecord(record);
  }

  /**
   * Log the output / result of a tool execution
   */
  async logToolResult(
    toolCall: HistoryToolCall,
    output: HistoryToolOutput,
    message: Message,
    turn: number
  ): Promise<HistoryRecord> {
    const record: HistoryRecord = {
      ...this.createBaseRecord("tool_result", turn),
      toolCall,
      tool_call: toolCall,
      output,
      message: {
        role: message.role,
        content: message.content,
        timestamp: message.timestamp,
      },
      durationMs: output.durationMs,
      response: null,
    };

    return this.appendRecord(record);
  }

  /**
   * Log the final summary of the completed session
   */
  async logSessionSummary(summary: SessionSummary): Promise<HistoryRecord> {
    const record: HistoryRecord = {
      ...this.createBaseRecord("session_summary", this.currentTurn),
      response: {
        text: summary.finalResponse,
      },
      toolCalls: summary.toolCallsExecuted.map((tc, index) => ({
        id: `call_${index + 1}`,
        name: tc.name,
        args: tc.args,
      })),
      usage: summary.totalUsage,
      durationMs: summary.durationMs,
      error: summary.error,
      metadata: {
        ...this.metadata,
        status: summary.status,
        totalTurns: summary.totalTurns,
        toolCallsCount: summary.toolCallsExecuted.length,
      },
    };

    return this.appendRecord(record);
  }

  /**
   * Log an unhandled error during session execution
   */
  async logError(error: unknown, turn: number = this.currentTurn): Promise<HistoryRecord> {
    const errObj =
      error instanceof Error
        ? { message: error.message, stack: error.stack }
        : { message: String(error) };

    const record: HistoryRecord = {
      ...this.createBaseRecord("error", turn),
      error: errObj,
    };

    return this.appendRecord(record);
  }

  /**
   * Await pending file writes
   */
  async flush(): Promise<void> {
    await this.writePromise;
  }
}

/**
 * ConversationLogger: Manages conversation history directories and sessions.
 */
export class ConversationLogger {
  readonly baseDir: string;
  readonly enabled: boolean;

  constructor(config?: HarnessHistoryConfig) {
    this.baseDir = path.resolve(
      process.cwd(),
      config?.baseDir || process.env.HARNESS_HISTORY_DIR || ".First_harness/conversationHistory/brain"
    );
    this.enabled = config?.enabled ?? true;
  }

  /**
   * Create a new SessionLogger for tracking a session
   */
  createSession(options: CreateSessionOptions): SessionLogger {
    return new SessionLogger(this.baseDir, options);
  }

  /**
   * Get the expected path of a session file
   */
  getSessionFilePath(sessionId: string, date: string = getLocalDateString()): string {
    return path.join(this.baseDir, date, `${sessionId}.jsonl`);
  }

  /**
   * Read all JSONL records for a given session.
   * If date is not specified, searches across all date directories.
   */
  async readSessionHistory(
    sessionId: string,
    date?: string
  ): Promise<HistoryRecord[]> {
    let targetFile: string | undefined;

    if (date) {
      targetFile = this.getSessionFilePath(sessionId, date);
    } else {
      // Find across date directories
      try {
        const dates = await fs.readdir(this.baseDir);
        for (const d of dates) {
          const candidate = path.join(this.baseDir, d, `${sessionId}.jsonl`);
          try {
            const stat = await fs.stat(candidate);
            if (stat.isFile()) {
              targetFile = candidate;
              break;
            }
          } catch {
            // continue searching
          }
        }
      } catch {
        return [];
      }
    }

    if (!targetFile) {
      return [];
    }

    try {
      const content = await fs.readFile(targetFile, "utf-8");
      const lines = content.split("\n").filter((l) => l.trim().length > 0);
      return lines.map((line) => JSON.parse(line) as HistoryRecord);
    } catch {
      return [];
    }
  }

  /**
   * List all stored sessions, optionally filtered by date (YYYY-MM-DD)
   */
  async listSessions(
    date?: string
  ): Promise<Array<{ date: string; sessionId: string; filePath: string; sizeBytes: number }>> {
    const results: Array<{ date: string; sessionId: string; filePath: string; sizeBytes: number }> = [];

    try {
      const dateDirs = date ? [date] : await fs.readdir(this.baseDir);

      for (const d of dateDirs) {
        const dirPath = path.join(this.baseDir, d);
        try {
          const stat = await fs.stat(dirPath);
          if (!stat.isDirectory()) continue;

          const files = await fs.readdir(dirPath);
          for (const file of files) {
            if (file.endsWith(".jsonl")) {
              const filePath = path.join(dirPath, file);
              const fileStat = await fs.stat(filePath);
              const sessionId = file.replace(/\.jsonl$/, "");
              results.push({
                date: d,
                sessionId,
                filePath,
                sizeBytes: fileStat.size,
              });
            }
          }
        } catch {
          // Ignore invalid directories
        }
      }
    } catch {
      // Base directory does not exist yet
    }

    return results;
  }
}
