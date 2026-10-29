import asyncio
import json
from collections import deque
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel

from pi_in_py.agent.agent import Agent
from pi_in_py.agent.compaction import (
    SUMMARY_PREFIX,
    SUMMARY_PROMPT,
    Compaction,
    project_context,
)
from pi_in_py.agent.types import AgentEvent, define_tool
from pi_in_py.ai.context import estimate_context
from pi_in_py.ai.types import (
    AssistantMessage,
    Message,
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
from pi_in_py.app.session import CompactionRecord, SessionStore


class SummaryProvider:
    def __init__(
        self,
        summary: AssistantMessage | Exception | None = None,
        answers: Sequence[AssistantMessage] = (),
    ) -> None:
        self.summary = summary or AssistantMessage((Text("Goal: inspect code. Keep findings."),))
        self.answers = deque(answers)
        self.requests: list[Request] = []
        self.started = asyncio.Event()
        self.block = False
        self.closed = False

    @property
    def summary_requests(self) -> list[Request]:
        return [r for r in self.requests if r.messages[0] == SystemMessage(SUMMARY_PROMPT)]

    @property
    def normal_requests(self) -> list[Request]:
        return [r for r in self.requests if r not in self.summary_requests]

    async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
        self.requests.append(request)
        if request.messages[0] == SystemMessage(SUMMARY_PROMPT):
            self.started.set()
            try:
                if self.block:
                    await asyncio.Event().wait()
                if isinstance(self.summary, Exception):
                    raise self.summary
                yield TextDelta("hidden summary delta")
                yield ResponseCompleted(self.summary)
            finally:
                self.closed = True
        else:
            answer = (
                self.answers.popleft()
                if self.answers
                else AssistantMessage((Text("Answer"),), usage=Usage(80, 20))
            )
            yield ResponseCompleted(answer)


def history() -> tuple[Message, ...]:
    return (
        SystemMessage("Original instructions"),
        UserMessage("Old goal " + "x" * 5000),
        AssistantMessage((Text("Old findings " + "y" * 5000),)),
        UserMessage("Continue"),
        AssistantMessage((Text("Latest completed turn"),)),
    )


def test_compaction_preserves_transcript_pending_prompt_and_resumes(tmp_path: Path) -> None:
    async def case() -> None:
        provider = SummaryProvider()
        original = history()
        store = SessionStore(tmp_path / "session.jsonl", tmp_path)
        store.append_turn(original)
        prefix = store.path.read_bytes()
        events: list[AgentEvent] = []
        agent = Agent(
            provider,
            messages=original,
            context_window=2048,
            save_compaction=store.append_compaction,
        )

        async def observe(event: AgentEvent) -> None:
            events.append(event)
            if event.type == "turn_end":
                store.append_turn(event.messages)

        agent.subscribe(observe)
        assert (await agent.prompt("Verbatim pending question")).reason == "complete"
        assert agent.messages[: len(original)] == original
        assert agent.compaction is not None
        assert agent.compaction.through_message_count == len(original)
        sent = provider.normal_requests[0]
        assert sent.messages[0] == original[0]
        assert sent.messages[1] == UserMessage(SUMMARY_PREFIX + agent.compaction.summary)
        assert sent.messages[-1] == UserMessage("Verbatim pending question")
        assert original[-1] in sent.messages
        assert len(provider.summary_requests) > 1  # Long history is chunked for the small model.
        for request in provider.summary_requests:
            assert not request.tools
            assert request.max_output_tokens == 256
            assert estimate_context(request.messages)[0] + 256 + 128 <= 2048
        assert not any(event.delta == "hidden summary delta" for event in events)
        assert any(
            e.type == "context_update"
            and e.context_usage is not None
            and e.context_usage.tokens is None
            for e in events
        )
        assert agent.get_context_usage().tokens == 100
        assert store.path.read_bytes().startswith(prefix)
        loaded = SessionStore(store.path, tmp_path)
        assert loaded.messages == agent.messages
        assert loaded.compaction == agent.compaction
        resumed = Agent(
            SummaryProvider(),
            messages=loaded.messages,
            context_window=2048,
            compaction=loaded.compaction,
        )
        assert resumed.context_messages == agent.context_messages
        assert resumed.get_context_usage().tokens is None

    asyncio.run(case())


@pytest.mark.parametrize(
    "summary",
    [
        AssistantMessage((Text(""),)),
        AssistantMessage((Text("partial"),), "length"),
        AssistantMessage((ToolCall("c", "read", {}),), "tool_use"),
        AssistantMessage((Text("z" * 2000),)),
        RuntimeError("summary request failed"),
    ],
)
def test_failed_summary_preserves_previous_context(summary: AssistantMessage | Exception) -> None:
    async def case() -> None:
        provider = SummaryProvider(summary)
        original = history()
        agent = Agent(provider, messages=original, context_window=2048)
        events: list[AgentEvent] = []

        async def observe(event: AgentEvent) -> None:
            events.append(event)

        agent.subscribe(observe)
        assert (await agent.prompt("pending")).reason == "error"
        assert agent.messages == original and agent.compaction is None
        assert not provider.normal_requests
        assert any(e.type == "compaction_end" and e.error for e in events)

    asyncio.run(case())


def test_cancelled_summary_closes_stream_and_preserves_queues() -> None:
    async def case() -> None:
        provider = SummaryProvider()
        provider.block = True
        agent = Agent(provider, messages=history(), context_window=2048)
        agent.steer("queued steering")
        agent.follow_up("queued followup")
        task = agent.start("initial input")
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        agent.cancel()
        assert (await task).reason == "cancelled"
        assert provider.closed and agent.messages == history() and agent.compaction is None
        assert agent.queued_messages == (1, 1)

    asyncio.run(case())


def test_failed_persistence_rolls_back_record_and_does_not_activate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case() -> None:
        store = SessionStore(tmp_path / "s.jsonl", tmp_path)
        store.append_turn(history())
        original = store.path.read_bytes()

        def fail_fsync(_: int) -> None:
            raise OSError("disk failure")

        monkeypatch.setattr("pi_in_py.app.session.os.fsync", fail_fsync)
        agent = Agent(
            SummaryProvider(),
            messages=store.messages,
            context_window=2048,
            save_compaction=store.append_compaction,
        )
        result = await agent.prompt("next")
        assert result.reason == "error" and result.error == "disk failure"
        assert agent.compaction is None and store.compaction is None
        assert store.path.read_bytes() == original
        assert SessionStore(store.path, tmp_path).compaction is None

    asyncio.run(case())


def test_newest_tool_batch_stays_intact_and_oversized_result_stops() -> None:
    async def case() -> None:
        for size, expected in [(20, "complete"), (8000, "error")]:
            provider = SummaryProvider()
            original = (
                *history()[:-1],
                AssistantMessage((ToolCall("c", "read", {}),), "tool_use"),
                ToolResult("c", "read", "r" * size),
            )
            agent = Agent(provider, messages=original, context_window=2048)
            assert (await agent.prompt("pending")).reason == expected
            if expected == "complete":
                sent = provider.normal_requests[0].messages
                assert sent[-3:-1] == original[-2:]
                assert agent.compaction is not None
                assert not isinstance(original[agent.compaction.first_kept_index], ToolResult)
            else:
                assert agent.messages == original and agent.compaction is None

    asyncio.run(case())


def test_repeated_compaction_includes_previous_summary_and_roundtrips(tmp_path: Path) -> None:
    async def case() -> None:
        store = SessionStore(tmp_path / "s.jsonl", tmp_path)
        store.append_turn(history())
        provider = SummaryProvider(
            answers=[
                AssistantMessage((Text("a" * 4000),), usage=Usage(2000, 1000)),
                AssistantMessage((Text("done"),), usage=Usage(100, 10)),
            ]
        )
        agent = Agent(
            provider,
            messages=store.messages,
            context_window=2048,
            save_compaction=store.append_compaction,
        )

        async def save(event: AgentEvent) -> None:
            if event.type == "turn_end":
                store.append_turn(event.messages)

        agent.subscribe(save)
        assert (await agent.prompt("first")).reason == "complete"
        first = agent.compaction
        assert first is not None
        requests_before = len(provider.summary_requests)
        assert (await agent.prompt("second")).reason == "complete"
        second = agent.compaction
        assert second is not None and second.first_kept_index > first.first_kept_index
        request = provider.summary_requests[requests_before]
        assert isinstance(request.messages[1], UserMessage)
        assert first.summary in request.messages[1].content
        loaded = SessionStore(store.path, tmp_path)
        assert loaded.compaction == second
        assert loaded.messages == agent.messages
        assert project_context(loaded.messages, loaded.compaction) == agent.context_messages

    asyncio.run(case())


@pytest.mark.parametrize("cut,through", [(0, 5), (4, 6), (5, 5)])
def test_corrupt_compaction_records_are_rejected(tmp_path: Path, cut: int, through: int) -> None:
    store = SessionStore(tmp_path / "s.jsonl", tmp_path)
    store.append_turn(history())
    record = CompactionRecord(compaction=Compaction("summary", cut, through))
    with store.path.open("a", encoding="utf-8") as file:
        file.write(record.model_dump_json() + "\n")
    with pytest.raises(ValueError, match="line 3"):
        SessionStore(store.path, tmp_path)


def test_store_rejects_split_tool_results_and_backward_boundaries(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.jsonl", tmp_path)
    messages = (
        *history(),
        AssistantMessage((ToolCall("c", "read", {}),), "tool_use"),
        ToolResult("c", "read", "data"),
    )
    store.append_turn(messages)
    with pytest.raises(ValueError, match="split"):
        store.append_compaction(Compaction("summary", 6, len(messages)))
    store.append_compaction(Compaction("summary", 3, len(messages)))
    with pytest.raises(ValueError, match="advance"):
        store.append_compaction(Compaction("summary", 2, len(messages)))
    assert json.loads(store.path.read_text().splitlines()[-1])["type"] == "compaction"


def test_no_history_and_oversized_pending_prompt_are_preserved() -> None:
    async def case() -> None:
        provider = SummaryProvider()
        agent = Agent(provider, context_window=1024)
        before = agent.messages
        result = await agent.prompt("x" * 8000)
        assert result.reason == "error" and "No older committed history" in (result.error or "")
        assert agent.messages == before and not provider.requests

    asyncio.run(case())


@pytest.mark.parametrize("reported,expect_compaction", [(1531, False), (1532, True)])
def test_threshold_is_strict_and_uses_fresh_provider_usage(
    reported: int, expect_compaction: bool
) -> None:
    async def case() -> None:
        original = (
            SystemMessage("system"),
            UserMessage("x" * 2000),
            AssistantMessage((Text("y" * 2000),)),
            UserMessage("recent"),
            AssistantMessage((Text("recent response"),)),
        )
        provider = SummaryProvider(
            answers=[
                AssistantMessage((Text("first answer"),), usage=Usage(reported)),
                AssistantMessage((Text("second answer"),), usage=Usage(100)),
            ]
        )
        agent = Agent(provider, messages=original, context_window=2048)
        assert (await agent.prompt("first")).reason == "complete"
        assert agent.compaction is None
        assert (await agent.prompt("next")).reason == "complete"
        assert bool(provider.summary_requests) == expect_compaction

    asyncio.run(case())


def test_disabling_automatic_compaction_still_tracks_and_limits_output() -> None:
    async def case() -> None:
        provider = SummaryProvider()
        agent = Agent(provider, messages=history(), context_window=4096, auto_compact=False)
        assert (await agent.prompt("p" * 4000)).reason == "complete"
        assert not provider.summary_requests and len(provider.normal_requests) == 1
        output_limit = provider.normal_requests[0].max_output_tokens
        assert output_limit is not None and 0 < output_limit < 4096
        assert not agent.get_context_usage().auto_compact

    asyncio.run(case())


def test_compaction_after_tool_batch_does_not_replay_tools_or_consume_steering() -> None:
    class NoArgs(BaseModel):
        pass

    async def case() -> None:
        executed: list[str] = []
        original = (
            SystemMessage("system"),
            UserMessage("old goal " + "x" * 2000),
            AssistantMessage((Text("findings " + "y" * 2000),)),
        )
        provider = SummaryProvider(
            answers=[
                AssistantMessage((ToolCall("c", "read", {}),), "tool_use", Usage(1000, 20)),
                AssistantMessage((Text("done"),), usage=Usage(1500, 10)),
            ]
        )

        async def execute(_: NoArgs) -> str:
            executed.append("read")
            agent.steer("Preserve this steering instruction")
            return "tool contents " + "r" * 9000

        agent = Agent(
            provider,
            [define_tool("read", "Read", NoArgs, execute)],
            messages=original,
            context_window=4096,
        )
        assert (await agent.prompt("read file")).reason == "complete"
        assert executed == ["read"] and agent.compaction is not None
        assert len(provider.normal_requests) == 2
        second = provider.normal_requests[1].messages
        assert second[-1] == UserMessage("Preserve this steering instruction")
        assert isinstance(second[-2], ToolResult)
        assert isinstance(second[-3], AssistantMessage)
        assert agent.compaction.through_message_count == len(original) + 3

    asyncio.run(case())


def test_ineffective_summary_is_rejected_without_changing_context() -> None:
    async def case() -> None:
        original = (
            SystemMessage("system"),
            UserMessage("precise user constraint"),
            AssistantMessage((Text("acknowledged"),)),
            UserMessage("recent"),
            AssistantMessage((Text("recent answer"),)),
        )
        provider = SummaryProvider(
            summary=AssistantMessage((Text("summary " * 100),)),
            answers=[AssistantMessage((Text("first answer"),), usage=Usage(2000))],
        )
        agent = Agent(provider, messages=original, context_window=2048)
        assert (await agent.prompt("first")).reason == "complete"
        before = agent.messages
        result = await agent.prompt("second")
        assert result.reason == "error" and "could not free enough space" in (result.error or "")
        assert agent.messages == before and agent.compaction is None
        assert len(provider.normal_requests) == 1

    asyncio.run(case())


def test_cancelling_later_compaction_preserves_saved_summary() -> None:
    async def case() -> None:
        provider = SummaryProvider(
            answers=[
                AssistantMessage((Text("a" * 4000),), usage=Usage(2000, 1000)),
            ]
        )
        agent = Agent(provider, messages=history(), context_window=2048)
        assert (await agent.prompt("first")).reason == "complete"
        first = agent.compaction
        before = agent.messages
        provider.block = True
        provider.started.clear()
        task = agent.start("second")
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        agent.cancel()
        assert (await task).reason == "cancelled"
        assert agent.compaction == first and agent.messages == before

    asyncio.run(case())
