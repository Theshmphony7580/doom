from collections.abc import AsyncGenerator
from typing import Literal

from anthropic import AsyncAnthropic
from anthropic.types import ContentBlockParam, MessageParam, ToolParam
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


def to_messages(request: Request) -> tuple[str, list[MessageParam]]:
    system: list[str] = []
    messages: list[MessageParam] = []
    for message in request.messages:
        if isinstance(message, SystemMessage):
            system.append(message.content)
        elif isinstance(message, UserMessage):
            messages.append({"role": "user", "content": message.content})
        elif isinstance(message, ToolResult):
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                            "is_error": message.is_error,
                        }
                    ],
                }
            )
        else:
            content: list[ContentBlockParam] = []
            for block in message.content:
                if isinstance(block, Text):
                    content.append({"type": "text", "text": block.text})
                else:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": dict(block.arguments),
                        }
                    )
            messages.append({"role": "assistant", "content": content})
    return "\n\n".join(system), messages


class AnthropicProvider:
    def __init__(self, client: AsyncAnthropic, model: str, max_tokens: int = 4096) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
        system, messages = to_messages(request)
        tools: list[ToolParam] = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
            for tool in request.tools
        ]
        # SDK retries apply to requests, never to tool execution.
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=request.max_output_tokens or self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            thinking={"type": "disabled"},
        ) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield TextDelta(event.delta.text)
            final = await stream.get_final_message()

        content: list[Content] = []
        for block in final.content:
            if block.type == "text":
                content.append(Text(block.text))
            elif block.type == "tool_use":
                content.append(
                    ToolCall(
                        block.id, block.name, _ARGUMENTS.validate_python(block.input, strict=True)
                    )
                )
            else:
                raise ProviderError(f"Unsupported response block: {block.type}")

        reason: Literal["stop", "tool_use", "length"]
        if final.stop_reason in ("end_turn", "stop_sequence"):
            reason = "stop"
        elif final.stop_reason == "tool_use":
            reason = "tool_use"
        elif final.stop_reason in ("max_tokens", "model_context_window_exceeded"):
            reason = "length"
        else:
            raise ProviderError(f"Unsupported provider stop reason: {final.stop_reason}")
        yield ResponseCompleted(
            AssistantMessage(
                tuple(content),
                reason,
                Usage(
                    final.usage.input_tokens,
                    final.usage.output_tokens,
                    final.usage.cache_read_input_tokens or 0,
                    final.usage.cache_creation_input_tokens or 0,
                ),
            )
        )
