import {
  createModels,
  type Api,
  type AssistantMessage,
  type Message,
  type Model,
  type MutableModels,
  type Provider,
  type Tool,
  type Usage,
} from "@earendil-works/pi-ai";
import type { AgentDefinition, HarnessConfig, HarnessTool } from "./types.ts";
import {
  ConversationLogger,
  SessionLogger,
  generateSessionId,
  type HistoryRecord,
  type HistoryToolCall,
} from "./history/index.ts";

export interface HarnessRunCallbacks {
  onTextDelta?: (delta: string) => void;
  onThinkingDelta?: (delta: string) => void;
  onToolCallStart?: (toolName: string, args: Record<string, unknown>) => void;
  onToolCallEnd?: (toolName: string, resultSummary: string) => void;
}

export interface HarnessRunOptions {
  apiKey?: string;
  temperature?: number;
  maxTurns?: number;
  callbacks?: HarnessRunCallbacks;
  signal?: AbortSignal;
  sessionId?: string;
  userId?: string;
  userName?: string;
  enableHistory?: boolean;
  date?: string;
  metadata?: Record<string, unknown>;
}

export interface HarnessRunResult {
  sessionId: string;
  historyFilePath?: string;
  finalResponse: string;
  toolCallsExecuted: Array<{
    name: string;
    args: Record<string, unknown>;
    isError: boolean;
  }>;
  totalUsage: Usage;
  durationMs: number;
}

/**
 * Harness: The core execution and evaluation engine for AI models and agent tools
 */
export class Harness {
  readonly models: MutableModels;
  private tools = new Map<string, HarnessTool>();
  private agents = new Map<string, AgentDefinition>();
  readonly historyLogger: ConversationLogger;

  constructor(config?: Partial<HarnessConfig>) {
    this.models = createModels();
    this.historyLogger = new ConversationLogger(config?.historyConfig);

    if (config?.tools) {
      for (const tool of config.tools) {
        this.registerTool(tool);
      }
    }

    if (config?.agents) {
      for (const agent of Object.values(config.agents)) {
        this.registerAgent(agent);
      }
    }
  }

  /**
   * Register an AI model provider (e.g. Groq, Anthropic, OpenAI)
   */
  registerProvider(provider: Provider): this {
    this.models.setProvider(provider);
    return this;
  }

  /**
   * Register a tool with the harness
   */
  registerTool(tool: HarnessTool): this {
    this.tools.set(tool.name, tool);
    return this;
  }

  /**
   * Register an agent definition
   */
  registerAgent(agent: AgentDefinition): this {
    this.agents.set(agent.id, agent);
    return this;
  }

  /**
   * Retrieve a registered model
   */
  getModel(providerId: string, modelId: string): Model<Api> | undefined {
    return this.models.getModel(providerId, modelId);
  }

  /**
   * Retrieve a registered agent
   */
  getAgent(agentId: string): AgentDefinition | undefined {
    return this.agents.get(agentId);
  }

  /**
   * Get all registered agents
   */
  getAllAgents(): AgentDefinition[] {
    return Array.from(this.agents.values());
  }

  /**
   * Get all registered tools
   */
  getAllTools(): HarnessTool[] {
    return Array.from(this.tools.values());
  }

  /**
   * Get all registered tools for an agent
   */
  getAgentTools(agent: AgentDefinition): HarnessTool[] {
    if (!agent.tools || agent.tools.length === 0) {
      return Array.from(this.tools.values());
    }
    return agent.tools
      .map((name) => this.tools.get(name))
      .filter((tool): tool is HarnessTool => tool !== undefined);
  }

  /**
   * Read history records for a session
   */
  async getConversationHistory(sessionId: string, date?: string): Promise<HistoryRecord[]> {
    return this.historyLogger.readSessionHistory(sessionId, date);
  }

  /**
   * List all tracked sessions
   */
  async listSessions(date?: string) {
    return this.historyLogger.listSessions(date);
  }

  /**
   * Create an ongoing conversational session with an agent for multi-turn interactions.
   */
  createSession(agentId: string, options?: HarnessRunOptions): AgentSession {
    const agent = this.agents.get(agentId);
    if (!agent) {
      throw new Error(`Agent "${agentId}" is not registered in the harness.`);
    }

    const model = this.getModel(agent.model.providerId, agent.model.modelId);
    if (!model) {
      throw new Error(
        `Model "${agent.model.modelId}" for provider "${agent.model.providerId}" not found in harness.`
      );
    }

    return new AgentSession(this, agent, model, options);
  }

  /**
   * Execute an agent on a user prompt with automated multi-turn tool calling and date-wise JSONL history tracking
   */
  async runAgent(
    agentId: string,
    prompt: string,
    options?: HarnessRunOptions
  ): Promise<HarnessRunResult> {
    const session = this.createSession(agentId, options);
    try {
      const result = await session.prompt(prompt, options);
      await session.close("completed");
      return {
        ...result,
        totalUsage: session.totalUsage,
        toolCallsExecuted: session.totalToolCalls,
        durationMs: session.sessionDurationMs,
      };
    } catch (error) {
      try {
        await session.close("error", error);
      } catch {
        // ignore
      }
      throw error;
    }
  }
}

/**
 * AgentSession: Manages an active multi-turn conversation session with history persistence,
 * tool calling loop, and cumulative token/duration tracking.
 */
export class AgentSession {
  readonly harness: Harness;
  readonly agent: AgentDefinition;
  readonly model: Model<Api>;
  readonly sessionId: string;
  readonly sessionLogger: SessionLogger | null;
  readonly options: HarnessRunOptions;

  private _messages: Message[] = [];
  private _totalUsage: Usage = {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
  private _toolCallsExecuted: HarnessRunResult["toolCallsExecuted"] = [];
  private _sessionStartTime: number;
  private _currentTurn = 0;
  private _isClosed = false;
  private _lastAssistantResponse = "";

  constructor(
    harness: Harness,
    agent: AgentDefinition,
    model: Model<Api>,
    options?: HarnessRunOptions
  ) {
    this.harness = harness;
    this.agent = agent;
    this.model = model;
    this.options = options || {};
    this._sessionStartTime = Date.now();

    // Fix compatibility: providers like Groq Jinja templates require system role over developer role
    if (this.agent.model.providerId === "groq") {
      (this.model as any).compat = {
        ...((this.model as any).compat || {}),
        supportsDeveloperRole: false,
      };
    }

    const isHistoryEnabled = this.options.enableHistory ?? this.harness.historyLogger.enabled;
    this.sessionLogger = isHistoryEnabled
      ? this.harness.historyLogger.createSession({
          sessionId: this.options.sessionId,
          date: this.options.date,
          user: {
            id: this.options.userId || process.env.USER || process.env.USERNAME || "default_user",
            name: this.options.userName || "User",
          },
          agent: {
            id: this.agent.id,
            description: this.agent.description,
          },
          model: {
            providerId: this.agent.model.providerId,
            modelId: this.agent.model.modelId,
            temperature: this.options.temperature,
          },
          metadata: this.options.metadata,
        })
      : null;

    this.sessionId = this.sessionLogger
      ? this.sessionLogger.sessionId
      : (this.options.sessionId || generateSessionId());
  }

  get historyFilePath(): string | undefined {
    return this.sessionLogger?.filePath;
  }

  get messages(): Message[] {
    return this._messages;
  }

  get totalUsage(): Usage {
    return this._totalUsage;
  }

  get totalToolCalls(): HarnessRunResult["toolCallsExecuted"] {
    return this._toolCallsExecuted;
  }

  get sessionDurationMs(): number {
    return Date.now() - this._sessionStartTime;
  }

  get turnCount(): number {
    return this._currentTurn;
  }

  get isClosed(): boolean {
    return this._isClosed;
  }

  /**
   * Execute one turn with user prompt, multi-turn tool calling, streaming callbacks, and history logging.
   */
  async prompt(
    prompt: string,
    turnOptions?: HarnessRunOptions
  ): Promise<HarnessRunResult> {
    const startTime = Date.now();
    const mergedOptions = { ...this.options, ...turnOptions };
    const callbacks = turnOptions?.callbacks || this.options.callbacks;
    const activeTools = this.harness.getAgentTools(this.agent);
    const maxTurns = mergedOptions.maxTurns ?? 6;

    const userMessage: Message = {
      role: "user",
      content: prompt,
      timestamp: startTime,
    };
    this._messages.push(userMessage);

    // Log user message
    if (this.sessionLogger) {
      await this.sessionLogger.logUserMessage(prompt, userMessage, this._currentTurn + 1);
    }

    // Convert ToolDefinition to pi-ai Tool shape for the LLM request
    const llmTools: Tool[] = activeTools.map((t) => ({
      name: t.name,
      description: t.description,
      parameters: t.parameters,
    }));

    const toolCallsExecutedThisTurn: HarnessRunResult["toolCallsExecuted"] = [];
    const turnUsage: Usage = {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    };

    // Combine agent's system prompt with prompt guidelines from active tools
    const toolGuidelines = activeTools
      .flatMap((t) => t.promptGuidelines || [])
      .filter(Boolean);

    const effectiveSystemPrompt =
      toolGuidelines.length > 0
        ? `${this.agent.systemPrompt}\n\n### Tool Guidelines:\n${toolGuidelines.map((g) => `- ${g}`).join("\n")}`
        : this.agent.systemPrompt;

    let subTurn = 0;
    let finalAssistantText = "";

    try {
      while (subTurn < maxTurns) {
        subTurn++;
        this._currentTurn++;
        const turnNumber = this._currentTurn;
        let thinkingAccumulator = "";
        let result: AssistantMessage | null = null;
        let streamRetries = 0;
        const maxStreamRetries = 3;

        while (streamRetries <= maxStreamRetries) {
          try {
            thinkingAccumulator = "";
            const stream = this.harness.models.stream(
              this.model,
              {
                systemPrompt: effectiveSystemPrompt,
                messages: this._messages,
                tools: llmTools.length > 0 ? llmTools : undefined,
              },
              {
                apiKey: mergedOptions.apiKey,
                temperature: mergedOptions.temperature,
                signal: mergedOptions.signal,
              }
            );

            for await (const event of stream) {
              if (event.type === "text_delta") {
                callbacks?.onTextDelta?.(event.delta);
              } else if (event.type === "thinking_delta") {
                thinkingAccumulator += event.delta;
                callbacks?.onThinkingDelta?.(event.delta);
              } else if (event.type === "error") {
                throw new Error(`Model stream error: ${event.error.errorMessage || event.reason}`);
              }
            }

            result = await stream.result();
            break;
          } catch (streamError: any) {
            const errMsg = streamError?.message || String(streamError);
            const isRateLimit = errMsg.includes("429") || errMsg.includes("rate_limit_exceeded");
            if (isRateLimit && streamRetries < maxStreamRetries) {
              streamRetries++;
              const match = errMsg.match(/try again in ([\d\.]+)s/i);
              const waitSeconds = match ? Math.ceil(parseFloat(match[1])) + 1 : 6;
              console.log(
                `\n⏳ [Rate limit reached. Waiting ${waitSeconds}s before retrying turn ${turnNumber} (attempt ${streamRetries}/${maxStreamRetries})...]`
              );
              await new Promise((r) => setTimeout(r, waitSeconds * 1000));
              continue;
            }
            throw streamError;
          }
        }

        if (!result) {
          throw new Error("Model stream failed to produce a result.");
        }

        if (result.usage) {
          turnUsage.input += result.usage.input;
          turnUsage.output += result.usage.output;
          turnUsage.totalTokens += result.usage.totalTokens;

          this._totalUsage.input += result.usage.input;
          this._totalUsage.output += result.usage.output;
          this._totalUsage.totalTokens += result.usage.totalTokens;
        }

        if (result.stopReason === "error") {
          throw new Error(result.errorMessage || "Model generation failed with an error.");
        }

        // Append assistant's turn to conversation history
        this._messages.push(result);

        // Check if the assistant called any tools
        const toolCalls = result.content.filter(
          (part): part is Extract<typeof part, { type: "toolCall" }> => part.type === "toolCall"
        );

        const textParts = result.content
          .filter((p): p is Extract<typeof p, { type: "text" }> => p.type === "text")
          .map((p) => p.text);
        const assistantText = textParts.join("\n");

        if (this.sessionLogger) {
          await this.sessionLogger.logModelResponse(
            {
              text: assistantText,
              thinking: thinkingAccumulator || undefined,
              stopReason: result.stopReason,
              usage: result.usage,
            },
            result,
            turnNumber
          );
        }

        if (toolCalls.length === 0) {
          // No tool calls requested: extract text and finish
          finalAssistantText = assistantText;
          this._lastAssistantResponse = finalAssistantText;
          break;
        }

        // Execute each tool call sequentially
        for (const call of toolCalls) {
          const toolCallItem: HistoryToolCall = {
            id: call.id,
            name: call.name,
            args: call.arguments,
          };

          if (this.sessionLogger) {
            await this.sessionLogger.logToolCall(toolCallItem, turnNumber);
          }

          const toolStart = Date.now();
          const matchingTool = activeTools.find((t) => t.name === call.name);
          if (!matchingTool) {
            const errText = `Tool "${call.name}" is not available. Available tools: ${activeTools.map((t) => t.name).join(", ")}`;
            const toolMsg: Message = {
              role: "toolResult",
              toolCallId: call.id,
              toolName: call.name,
              content: [{ type: "text", text: errText }],
              isError: true,
              timestamp: Date.now(),
            };
            this._messages.push(toolMsg);
            const callRecord = { name: call.name, args: call.arguments, isError: true };
            toolCallsExecutedThisTurn.push(callRecord);
            this._toolCallsExecuted.push(callRecord);

            if (this.sessionLogger) {
              await this.sessionLogger.logToolResult(
                toolCallItem,
                {
                  toolCallId: call.id,
                  toolName: call.name,
                  content: toolMsg.content,
                  isError: true,
                  summary: errText,
                  durationMs: Date.now() - toolStart,
                },
                toolMsg,
                turnNumber
              );
            }
            continue;
          }

          callbacks?.onToolCallStart?.(call.name, call.arguments);

          const toolResult = await matchingTool.execute(
            call.id,
            call.arguments,
            mergedOptions.signal,
            undefined,
            {} as any
          );
          const toolDuration = Date.now() - toolStart;

          const callRecord = {
            name: call.name,
            args: call.arguments,
            isError: toolResult.isError ?? false,
          };
          toolCallsExecutedThisTurn.push(callRecord);
          this._toolCallsExecuted.push(callRecord);

          const details = toolResult.details as Record<string, unknown> | undefined;
          const summaryText = toolResult.isError
            ? "Error occurred"
            : `Returned ${details?.resultCount ?? "results"}`;

          callbacks?.onToolCallEnd?.(call.name, summaryText);

          const toolMsg: Message = {
            role: "toolResult",
            toolCallId: call.id,
            toolName: call.name,
            content: toolResult.content,
            isError: toolResult.isError ?? false,
            timestamp: Date.now(),
          };
          this._messages.push(toolMsg);

          if (this.sessionLogger) {
            await this.sessionLogger.logToolResult(
              toolCallItem,
              {
                toolCallId: call.id,
                toolName: call.name,
                content: toolResult.content,
                isError: toolResult.isError ?? false,
                summary: summaryText,
                durationMs: toolDuration,
              },
              toolMsg,
              turnNumber
            );
          }
        }
      }

      return {
        sessionId: this.sessionId,
        historyFilePath: this.historyFilePath,
        finalResponse: finalAssistantText,
        toolCallsExecuted: toolCallsExecutedThisTurn,
        totalUsage: turnUsage,
        durationMs: Date.now() - startTime,
      };
    } catch (error) {
      if (this.sessionLogger) {
        await this.sessionLogger.logError(error, this._currentTurn);
        await this.sessionLogger.flush();
      }
      throw error;
    }
  }

  /**
   * Finalize and close the session, writing session_summary to history.
   */
  async close(status: "completed" | "error" = "completed", error?: unknown): Promise<void> {
    if (this._isClosed) return;
    this._isClosed = true;

    if (this.sessionLogger) {
      await this.sessionLogger.logSessionSummary({
        finalResponse: this._lastAssistantResponse,
        toolCallsExecuted: this._toolCallsExecuted,
        totalUsage: this._totalUsage,
        durationMs: Date.now() - this._sessionStartTime,
        totalTurns: this._currentTurn,
        status,
        error: error instanceof Error ? { message: error.message, stack: error.stack } : undefined,
      });
      await this.sessionLogger.flush();
    }
  }
}
