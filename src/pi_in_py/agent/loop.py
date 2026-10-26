import asyncio
from contextlib import aclosing

from pi_in_py.agent.types import AgentEvent, EventSink, Tool
from pi_in_py.ai.types import (
    AssistantMessage,
    Message,
    Provider,
    ProviderError,
    Request,
    ResponseCompleted,
    TextDelta,
    ToolCall,
    ToolResult,
)


async def run_turn(
    provider: Provider,
    messages: tuple[Message, ...],
    tools: dict[str, Tool],
    emit: EventSink,
    tool_timeout: float,
) -> tuple[AssistantMessage, tuple[ToolResult, ...]]:
    response: AssistantMessage | None = None
    # Closing the iterator also closes a provider HTTP stream on cancellation.
    request = Request(messages, tuple(t.spec for t in tools.values()))
    async with aclosing(provider.stream(request)) as stream:
        async for event in stream:
            if response is not None:
                raise ProviderError("Provider emitted events after its final response")
            if isinstance(event, TextDelta):
                await emit(AgentEvent("text_delta", delta=event.text))
            elif isinstance(event, ResponseCompleted):
                response = event.message
    if response is None:
        raise ProviderError("Provider stream ended without a final response")

    calls = [block for block in response.content if isinstance(block, ToolCall)]
    ids = [call.id for call in calls]
    if any(not call.id or not call.name for call in calls) or len(ids) != len(set(ids)):
        raise ProviderError("Tool calls must have nonempty names and unique, nonempty IDs")
    if response.stop_reason != "length" and bool(calls) != (response.stop_reason == "tool_use"):
        raise ProviderError("Tool calls and provider stop reason disagree")
    await emit(AgentEvent("message_end", message=response))

    results: list[ToolResult] = []
    for call in calls:
        await emit(AgentEvent("tool_start", call=call))
        try:
            if response.stop_reason == "length":
                raise ValueError(
                    "Not executed: the response was truncated; arguments may be incomplete"
                )
            tool = tools.get(call.name)
            if tool is None:
                raise ValueError(f"Unknown tool: {call.name}")
            async with asyncio.timeout(tool_timeout):
                content = await tool.execute(call.arguments)
            result = ToolResult(call.id, call.name, content)
        except TimeoutError:
            result = ToolResult(call.id, call.name, "Tool execution timed out", is_error=True)
        except Exception as error:
            result = ToolResult(call.id, call.name, str(error), is_error=True)
        results.append(result)
        await emit(AgentEvent("tool_end", call=call, result=result))
        await emit(AgentEvent("message_end", message=result))
    return response, tuple(results)
