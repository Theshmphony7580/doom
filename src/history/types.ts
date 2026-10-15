import type { AssistantMessage, Message, Usage } from "@earendil-works/pi-ai";

export interface HistoryUser {
  id: string;
  name?: string;
  metadata?: Record<string, unknown>;
}

export interface HistoryModel {
  providerId: string;
  modelId: string;
  temperature?: number;
}

export interface HistoryAgent {
  id: string;
  description?: string;
}

export interface HistoryToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface HistoryToolOutput {
  toolCallId?: string;
  toolName?: string;
  content: unknown;
  isError: boolean;
  summary?: string;
  durationMs?: number;
}

export interface HistoryModelResponse {
  text: string;
  thinking?: string;
  stopReason?: string;
  usage?: Usage;
}

export interface HistoryMessage {
  role: string;
  content: unknown;
  timestamp?: number;
}

export type HistoryEventType =
  | "session_start"
  | "user_message"
  | "model_response"
  | "tool_call"
  | "tool_result"
  | "session_summary"
  | "error";

export interface HistoryRecord {
  id: string;
  sessionId: string;
  session_id: string; // snake_case alias for external parser compatibility
  timestamp: string; // ISO 8601 string
  timestampMs: number; // Unix epoch ms
  date: string; // YYYY-MM-DD
  step: number; // 1-based sequential step in the session
  turn: number; // Current agent turn
  type: HistoryEventType;
  user: HistoryUser;
  agent: HistoryAgent;
  model: HistoryModel;
  message?: HistoryMessage | null;
  toolCall?: HistoryToolCall | null;
  tool_call?: HistoryToolCall | null; // alias
  toolCalls?: HistoryToolCall[];
  output?: HistoryToolOutput | null;
  response?: HistoryModelResponse | null;
  usage?: Usage;
  durationMs?: number;
  error?: {
    message: string;
    stack?: string;
  };
  metadata?: Record<string, unknown>;
}

export interface HarnessHistoryConfig {
  baseDir?: string;
  enabled?: boolean;
}

export interface CreateSessionOptions {
  sessionId?: string;
  date?: string;
  user?: HistoryUser;
  agent: HistoryAgent;
  model: HistoryModel;
  metadata?: Record<string, unknown>;
}

export interface SessionSummary {
  finalResponse: string;
  toolCallsExecuted: Array<{
    name: string;
    args: Record<string, unknown>;
    isError: boolean;
  }>;
  totalUsage: Usage;
  durationMs: number;
  totalTurns: number;
  status: "completed" | "error";
  error?: {
    message: string;
    stack?: string;
  };
}
