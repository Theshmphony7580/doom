from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, JsonValue

from pi_in_py.ai.context import ContextUsage
from pi_in_py.ai.types import Message, ToolCall, ToolResult, ToolSpec

type StopReason = Literal["complete", "cancelled", "error", "turn_limit", "length"]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    execute: Callable[[dict[str, JsonValue]], Awaitable[str]]


def define_tool[P: BaseModel](
    name: str,
    description: str,
    parameters: type[P],
    execute: Callable[[P], Awaitable[str]],
) -> Tool:
    async def validated_execute(arguments: dict[str, JsonValue]) -> str:
        return await execute(parameters.model_validate(arguments, strict=True))

    return Tool(ToolSpec(name, description, parameters.model_json_schema()), validated_execute)


@dataclass(frozen=True)
class AgentEvent:
    type: Literal[
        "run_start",
        "turn_start",
        "text_delta",
        "message_end",
        "tool_start",
        "tool_end",
        "turn_end",
        "run_end",
        "context_update",
        "compaction_start",
        "compaction_end",
    ]
    message: Message | None = None
    delta: str = ""
    call: ToolCall | None = None
    result: ToolResult | None = None
    messages: tuple[Message, ...] = ()
    reason: StopReason | None = None
    error: str | None = None
    context_usage: ContextUsage | None = None


type EventSink = Callable[[AgentEvent], Awaitable[None]]


@dataclass(frozen=True)
class RunResult:
    reason: StopReason
    messages: tuple[Message, ...]
    error: str | None = None
