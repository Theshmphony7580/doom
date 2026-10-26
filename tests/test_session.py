import asyncio
from pathlib import Path

import pytest

from pi_in_py.agent.agent import Agent
from pi_in_py.agent.types import AgentEvent
from pi_in_py.ai.fake import ScriptedProvider
from pi_in_py.ai.types import (
    AssistantMessage,
    SystemMessage,
    Text,
    ToolCall,
    ToolResult,
    UserMessage,
)
from pi_in_py.app.session import SessionStore


def test_completed_turns_roundtrip_and_resume(tmp_path: Path) -> None:
    async def case() -> None:
        path = tmp_path / "conversation.jsonl"
        store = SessionStore(path, tmp_path)
        agent = Agent(ScriptedProvider([AssistantMessage((Text("First"),))]))

        async def save(event: AgentEvent) -> None:
            if event.type == "turn_end":
                store.append_turn(event.messages)

        agent.subscribe(save)
        await agent.prompt("Hello")
        loaded = SessionStore(path, tmp_path)
        assert loaded.messages == agent.messages
        provider = ScriptedProvider([AssistantMessage((Text("Second"),))])
        resumed = Agent(provider, messages=loaded.messages)
        await resumed.prompt("Again")
        loaded.append_turn(resumed.messages)
        assert provider.requests[0].messages[:3] == agent.messages
        assert len(path.read_text(encoding="utf-8").splitlines()) == 3
        assert SessionStore(path, tmp_path).messages == resumed.messages

    asyncio.run(case())


def test_tool_calls_and_usage_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "session.jsonl", tmp_path)
    messages = (
        SystemMessage("System"),
        UserMessage("Read"),
        AssistantMessage((ToolCall("c", "read", {"path": "notes.txt"}),), "tool_use"),
        ToolResult("c", "read", "contents"),
    )
    store.append_turn(messages)
    assert SessionStore(store.path, tmp_path).messages == messages


def test_store_rejects_unresolved_calls_and_prefix_rewrites(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "session.jsonl", tmp_path)
    messages = (SystemMessage("System"), UserMessage("Hello"), AssistantMessage((Text("Hi"),)))
    store.append_turn(messages)
    with pytest.raises(ValueError, match="rewrite"):
        store.append_turn((SystemMessage("Changed"),))
    with pytest.raises(ValueError, match="unresolved"):
        store.append_turn((*messages, AssistantMessage((ToolCall("c", "read", {}),), "tool_use")))


def test_store_rejects_different_workspace_and_corruption(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    SessionStore(path, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="different workspace"):
        SessionStore(path, other)
    with path.open("a", encoding="utf-8") as file:
        file.write('{"type":"turn","messages":')
    with pytest.raises(ValueError, match="line 2"):
        SessionStore(path, tmp_path)


def test_empty_session_is_rejected_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.touch()
    with pytest.raises(ValueError, match="empty"):
        SessionStore(path, tmp_path)
    assert path.read_bytes() == b""


def test_cancellation_preserves_only_completed_turns_on_disk(tmp_path: Path) -> None:
    async def case() -> None:
        store = SessionStore(tmp_path / "session.jsonl", tmp_path)
        provider = ScriptedProvider(
            [
                AssistantMessage((Text("First"),)),
                AssistantMessage((Text("Second response is interrupted"),)),
            ]
        )
        agent = Agent(provider)
        agent.follow_up("Follow up")

        async def observe(event: AgentEvent) -> None:
            if event.type == "turn_end":
                store.append_turn(event.messages)
            elif event.type == "text_delta" and event.delta.startswith("Second"):
                agent.cancel()

        agent.subscribe(observe)
        assert (await agent.prompt("Begin")).reason == "cancelled"
        assert agent.messages[-1] == AssistantMessage((Text("First"),))
        assert SessionStore(store.path, tmp_path).messages == agent.messages
        assert agent.queued_messages == (0, 1)
        assert len(store.path.read_text(encoding="utf-8").splitlines()) == 2

    asyncio.run(case())
