import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pi_in_py.ai.types import AssistantMessage, Message, SystemMessage, ToolCall, ToolResult


class Header(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["header"] = "header"
    version: Literal[1] = 1
    workspace: str


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["turn"] = "turn"
    messages: tuple[Message, ...]


_RECORD = TypeAdapter(Annotated[Header | Turn, Field(discriminator="type")])


def validate_transcript(messages: Sequence[Message]) -> None:
    if not messages or not isinstance(messages[0], SystemMessage):
        raise ValueError("Session must start with a system message")
    pending: dict[str, str] = {}
    for index, message in enumerate(messages):
        if isinstance(message, ToolResult):
            if pending.pop(message.tool_call_id, None) != message.tool_name:
                raise ValueError("Tool result does not match a pending tool call")
        else:
            if pending:
                raise ValueError("Session has unresolved tool calls")
            if isinstance(message, SystemMessage) and index != 0:
                raise ValueError("v1 supports one leading system message")
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolCall):
                        if not block.id or block.id in pending:
                            raise ValueError("Session contains invalid tool-call IDs")
                        pending[block.id] = block.name
    if pending:
        raise ValueError("Session ends with unresolved tool calls")


class SessionStore:
    """One JSONL record per completed turn. One writer per session file in v1."""

    def __init__(self, path: Path, workspace: Path) -> None:
        self.path = path.resolve()
        self.workspace = workspace.resolve(strict=True)
        self.messages: tuple[Message, ...] = ()
        if self.path.exists():
            self._load()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(Header(workspace=str(self.workspace)).model_dump_json() + "\n")

    def _load(self) -> None:
        messages: list[Message] = []
        number = 0
        with self.path.open(encoding="utf-8") as file:
            for number, line in enumerate(file, 1):
                try:
                    record = _RECORD.validate_json(line, strict=True)
                    if number == 1:
                        if not isinstance(record, Header):
                            raise ValueError("Missing session header")
                        if Path(record.workspace).resolve() != self.workspace:
                            raise ValueError("Session belongs to a different workspace")
                    elif isinstance(record, Turn) and record.messages:
                        messages.extend(record.messages)
                        validate_transcript(messages)
                    else:
                        raise ValueError("Expected a nonempty turn record")
                except ValueError as error:
                    raise ValueError(
                        f"Invalid session {self.path}, line {number}: {error}"
                    ) from error
        if number == 0:
            raise ValueError("Session file is empty")
        self.messages = tuple(messages)

    def append_turn(self, messages: Sequence[Message]) -> None:
        snapshot = tuple(messages)
        if snapshot[: len(self.messages)] != self.messages:
            raise ValueError("Cannot rewrite a saved session prefix")
        new_messages = snapshot[len(self.messages) :]
        if not new_messages:
            return
        validate_transcript(snapshot)
        # A single record keeps an interrupted tool batch out of the saved transcript.
        record = Turn(messages=new_messages).model_dump_json() + "\n"
        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(record)
            file.flush()
            os.fsync(file.fileno())
        self.messages = snapshot
