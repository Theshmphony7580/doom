import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import type {
  AnyModel,
  Api,
  AssistantMessageEvent,
  AssistantMessageEventStream,
  Model,
} from "@earendil-works/pi-ai";
import type { OpenAICompletionsOptions } from "@earendil-works/pi-ai/api/openai-completions";

export type ProviderStreamEvent = AssistantMessageEvent;
export type ProviderStream = AssistantMessageEventStream;
export type ProviderModel = Model<Api>;
export type GroqModel = Model<"openai-completions">;
export type ProviderModelInfo = AnyModel;
export type CompletionOptions = OpenAICompletionsOptions;
export type { ToolDefinition };
