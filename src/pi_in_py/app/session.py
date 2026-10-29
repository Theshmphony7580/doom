import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from pi_in_py.agent.compaction import Compaction, validate_compaction
from pi_in_py.ai.types import AssistantMessage, Message, SystemMessage, ToolCall, ToolResult
from pi_in_py.app.prompt import prompt_hash


class Header(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["header"] = "header"
    version: Literal[1] = 1
    workspace: str


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["turn"] = "turn"
    messages: tuple[Message, ...]


class CompactionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["compaction"] = "compaction"
    compaction: Compaction


class PromptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["prompt"] = "prompt"
    content: str
    sha256: str

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if not self.content.strip() or prompt_hash(self.content) != self.sha256:
            raise ValueError("Invalid prompt content or SHA-256 hash")
        return self


_RECORD = TypeAdapter(
    Annotated[Header | Turn | CompactionRecord | PromptRecord, Field(discriminator="type")]
)


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
        self.compaction: Compaction | None = None
        self.prompt: PromptRecord | None = None
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
                    elif isinstance(record, CompactionRecord):
                        if record.compaction.through_message_count != len(messages):
                            raise ValueError("Compaction must cover the current saved history")
                        validate_compaction(record.compaction, messages, self.compaction)
                        self.compaction = record.compaction
                    elif isinstance(record, PromptRecord):
                        self.prompt = record
                    else:
                        raise ValueError("Expected a nonempty turn, compaction, or prompt record")
                except ValueError as error:
                    raise ValueError(
                        f"Invalid session {self.path}, line {number}: {error}"
                    ) from error
        if number == 0:
            raise ValueError("Session file is empty")
        self.messages = tuple(messages)

    def append_prompt(self, content: str) -> bool:
        record = PromptRecord(content=content, sha256=prompt_hash(content))
        if self.prompt == record:
            return False
        self._append_record(record.model_dump_json() + "\n")
        self.prompt = record
        return True

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
        self._append_record(record)
        self.messages = snapshot

    def append_compaction(self, compaction: Compaction) -> None:
        if compaction.through_message_count != len(self.messages):
            raise ValueError("Compaction must cover the current saved history")
        validate_compaction(compaction, self.messages, self.compaction)
        self._append_record(CompactionRecord(compaction=compaction).model_dump_json() + "\n")
        self.compaction = compaction

    def _append_record(self, record: str) -> None:
        with self.path.open("r+b") as file:
            offset = file.seek(0, os.SEEK_END)
            try:
                file.write(record.encode("utf-8"))
                file.flush()
                os.fsync(file.fileno())
            except OSError:
                # A failed append must not activate a complete-looking record on resume.
                file.seek(offset)
                file.truncate()
                file.flush()
                raise
