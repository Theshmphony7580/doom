import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI, omit
from openai.types.chat import (
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
)
from pydantic import JsonValue, TypeAdapter

from pi_in_py.ai.types import (
    AssistantMessage,
    Content,
    ProviderError,
    ProviderEvent,
    Request,
    ResponseCompleted,
    SystemMessage,
    Text,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
    UserMessage,
)

_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


def to_messages(request: Request) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = []
    for message in request.messages:
        if isinstance(message, SystemMessage):
            messages.append({"role": "system", "content": message.content})
        elif isinstance(message, UserMessage):
            messages.append({"role": "user", "content": message.content})
        elif isinstance(message, ToolResult):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": ("Tool error: " if message.is_error else "") + message.content,
                }
            )
        else:
            text = "".join(block.text for block in message.content if isinstance(block, Text))
            calls: list[ChatCompletionMessageFunctionToolCallParam] = [
                {
                    "type": "function",
                    "id": block.id,
                    "function": {"name": block.name, "arguments": json.dumps(block.arguments)},
                }
                for block in message.content
                if isinstance(block, ToolCall)
            ]
            if calls:
                messages.append({"role": "assistant", "content": text or None, "tool_calls": calls})
            else:
                messages.append({"role": "assistant", "content": text})
    return messages


@dataclass
class PendingToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


class OpenAIChatProvider:
    """Chat Completions adapter; the caller configures the SDK endpoint and key."""

    def __init__(self, client: AsyncOpenAI, model: str, max_tokens: int = 4096) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
        tools: list[ChatCompletionFunctionToolParam] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=to_messages(request),
            tools=tools or omit,
            max_completion_tokens=request.max_output_tokens or self.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        text: list[str] = []
        calls: dict[int, PendingToolCall] = {}
        finish_reason: str | None = None
        usage = Usage()
        async with stream:
            async for chunk in stream:
                if chunk.usage is not None:
                    details = chunk.usage.prompt_tokens_details
                    cache_read = (details.cached_tokens or 0) if details else 0
                    cache_write = (details.cache_write_tokens or 0) if details else 0
                    uncached = chunk.usage.prompt_tokens - cache_read - cache_write
                    if min(uncached, cache_read, cache_write, chunk.usage.completion_tokens) < 0:
                        raise ProviderError("Provider returned inconsistent token usage")
                    usage = Usage(
                        uncached,
                        chunk.usage.completion_tokens,
                        cache_read,
                        cache_write,
                    )
                if not chunk.choices:
                    continue
                if len(chunk.choices) != 1 or chunk.choices[0].index != 0:
                    raise ProviderError("Expected one completion choice")
                if finish_reason is not None:
                    raise ProviderError("Provider emitted response data after its finish reason")
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.refusal or delta.function_call or delta.audio:
                    raise ProviderError(
                        "Unsupported refusal, legacy function call, or audio response"
                    )
                if delta.content:
                    text.append(delta.content)
                    yield TextDelta(delta.content)
                for update in delta.tool_calls or ():
                    if update.index < 0 or update.type not in (None, "function"):
                        raise ProviderError("Unsupported tool-call delta")
                    call = calls.setdefault(update.index, PendingToolCall())
                    if update.id:
                        if call.id and call.id != update.id:
                            raise ProviderError("Tool-call ID changed during streaming")
                        call.id = update.id
                    if update.function:
                        call.name += update.function.name or ""
                        call.arguments += update.function.arguments or ""
                finish_reason = choice.finish_reason

        reason: Literal["stop", "tool_use", "length"]
        if finish_reason == "stop":
            reason = "stop"
        elif finish_reason == "tool_calls":
            reason = "tool_use"
        elif finish_reason == "length":
            reason = "length"
        else:
            raise ProviderError(f"Missing or unsupported finish reason: {finish_reason}")

        content: list[Content] = [Text("".join(text))] if text else []
        # Truncated arguments cannot be replayed safely; keep only the partial text.
        if reason != "length":
            for index in sorted(calls):
                call = calls[index]
                try:
                    arguments = _ARGUMENTS.validate_json(call.arguments, strict=True)
                except ValueError as error:
                    raise ProviderError(f"Invalid JSON arguments for tool {call.name}") from error
                content.append(ToolCall(call.id, call.name, arguments))
        yield ResponseCompleted(AssistantMessage(tuple(content), reason, usage))
