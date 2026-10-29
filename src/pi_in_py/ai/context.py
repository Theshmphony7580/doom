"""Provider-neutral context accounting. Estimates are not tokenizer measurements."""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from pi_in_py.ai.types import AssistantMessage, Message, Text, ToolResult, ToolSpec


@dataclass(frozen=True)
class ContextUsage:
    tokens: int | None
    estimated_tokens: int
    context_window: int | None
    percent: float | None
    estimated: bool
    auto_compact: bool


def estimate_message(message: Message) -> int:
    if isinstance(message, AssistantMessage):
        content = "".join(
            block.text if isinstance(block, Text) else json.dumps(asdict(block), ensure_ascii=False)
            for block in message.content
        )
    else:
        content = message.content
        if isinstance(message, ToolResult):
            content += message.tool_call_id + message.tool_name
    return (len(content) + 3) // 4 + 4


def estimate_context(
    messages: Sequence[Message], tools: Sequence[ToolSpec] = (), *, usage_start: int = 0
) -> tuple[int, bool]:
    trailing = 0
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            index >= usage_start
            and isinstance(message, AssistantMessage)
            and message.usage.total_tokens > 0
        ):
            return message.usage.total_tokens + trailing, trailing > 0
        trailing += estimate_message(message)
    tool_chars = (
        len(json.dumps([asdict(tool) for tool in tools], ensure_ascii=False)) if tools else 0
    )
    return trailing + (tool_chars + 3) // 4 + 3, True


def format_context(usage: ContextUsage) -> str:
    tokens = "?" if usage.tokens is None else f"{'~' if usage.estimated else ''}{usage.tokens:,}"
    capacity = f"{usage.context_window:,}" if usage.context_window is not None else "?"
    percent = f" ({usage.percent:.1f}%)" if usage.percent is not None else ""
    automatic = "auto" if usage.auto_compact else "auto off"
    return f"context={tokens}/{capacity}{percent} [{automatic}]"
