import type {
  ProviderConfig,
  ProviderModelConfig,
  ToolDefinition,
} from "@earendil-works/pi-coding-agent";

import type { HarnessHistoryConfig } from "./history/types.ts";

export interface ModelReference {
  providerId: string;
  modelId: string;
}

export interface AgentDefinition {
  id: string;
  description?: string;
  systemPrompt: string;
  model: ModelReference;
  tools?: string[];
}

export interface ProviderDefinition {
  id: string;
  config: ProviderConfig;
}

export type HarnessTool = ToolDefinition<any, any, any>;

export interface HarnessConfig {
  agents?: Record<string, AgentDefinition>;
  providers?: Record<string, ProviderDefinition>;
  tools?: HarnessTool[];
  historyConfig?: HarnessHistoryConfig;
}

export type { ProviderConfig, ProviderModelConfig, ToolDefinition };
export * from "./history/types.ts";

