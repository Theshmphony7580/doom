from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol

from pydantic import Field, JsonValue


@dataclass(frozen=True)
class Text:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, JsonValue]
    type: Literal["tool_call"] = "tool_call"


type Content = Annotated[Text | ToolCall, Field(discriminator="type")]


@dataclass(frozen=True)
class Usage:
    """Disjoint token counts: input_tokens excludes cache reads and writes."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass(frozen=True)
class SystemMessage:
    content: str
    role: Literal["system"] = "system"


@dataclass(frozen=True)
class UserMessage:
    content: str
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class AssistantMessage:
    content: tuple[Content, ...]
    stop_reason: Literal["stop", "tool_use", "length"] = "stop"
    usage: Usage = field(default_factory=Usage)
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False
    role: Literal["tool_result"] = "tool_result"


type Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolResult, Field(discriminator="role")
]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True)
class Request:
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ResponseCompleted:
    message: AssistantMessage


type ProviderEvent = TextDelta | ResponseCompleted


class ProviderError(RuntimeError):
    """The provider could not produce a complete, supported response."""


class Provider(Protocol):
    def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]: ...
