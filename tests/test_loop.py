import asyncio
from collections.abc import AsyncGenerator

import pytest
from pydantic import BaseModel, ConfigDict

from pi_in_py.agent.agent import Agent
from pi_in_py.agent.types import AgentEvent, Tool, define_tool
from pi_in_py.ai.fake import ScriptedProvider
from pi_in_py.ai.types import (
    AssistantMessage,
    ProviderEvent,
    Request,
    ResponseCompleted,
    Text,
    TextDelta,
    ToolCall,
    ToolResult,
    UserMessage,
)


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str


def echo_tool(calls: list[str]) -> Tool:
    async def execute(args: EchoArgs) -> str:
        calls.append(args.text)
        return args.text.upper()

    return define_tool("echo", "Uppercase text", EchoArgs, execute)


def test_model_tool_model_transcript_and_events() -> None:
    async def case() -> None:
        calls: list[str] = []
        provider = ScriptedProvider(
            [
                AssistantMessage((ToolCall("c1", "echo", {"text": "hello"}),), "tool_use"),
                AssistantMessage((Text("HELLO"),)),
            ]
        )
        agent = Agent(provider, [echo_tool(calls)])
        events: list[AgentEvent] = []

        async def observe(event: AgentEvent) -> None:
            events.append(event)

        agent.subscribe(observe)
        result = await agent.prompt("Uppercase hello")
        assert result.reason == "complete"
        assert calls == ["hello"]
        assert [m.role for m in agent.messages] == [
            "system",
            "user",
            "assistant",
            "tool_result",
            "assistant",
        ]
        assert provider.requests[1].messages[-1] == ToolResult("c1", "echo", "HELLO")
        assert "".join(e.delta for e in events) == "HELLO"
        assert [e.type for e in events].count("turn_end") == 2
        assert events[0].type == "run_start" and events[-1].type == "run_end"
        assert not agent.is_running

    asyncio.run(case())


@pytest.mark.parametrize(
    "call, expected",
    [
        (ToolCall("c", "missing", {}), "Unknown tool"),
        (ToolCall("c", "echo", {"text": 123}), "valid string"),
        (ToolCall("c", "echo", {"text": "x", "extra": True}), "Extra inputs"),
    ],
)
def test_invalid_tools_become_error_results(call: ToolCall, expected: str) -> None:
    async def case() -> None:
        calls: list[str] = []
        provider = ScriptedProvider(
            [AssistantMessage((call,), "tool_use"), AssistantMessage((Text("Recovered"),))]
        )
        agent = Agent(provider, [echo_tool(calls)])
        assert (await agent.prompt("Try a tool")).reason == "complete"
        result = provider.requests[1].messages[-1]
        assert isinstance(result, ToolResult) and result.is_error
        assert expected in result.content
        assert result.tool_call_id == call.id
        assert not calls

    asyncio.run(case())


def test_steering_waits_for_whole_batch_and_followup_waits_for_completion() -> None:
    async def case() -> None:
        executed: list[str] = []

        async def execute(args: EchoArgs) -> str:
            executed.append(args.text)
            if args.text == "first":
                agent.steer("Change direction")
                agent.follow_up("Summarize afterward")
            return args.text

        provider = ScriptedProvider(
            [
                AssistantMessage(
                    (
                        ToolCall("c1", "echo", {"text": "first"}),
                        ToolCall("c2", "echo", {"text": "second"}),
                    ),
                    "tool_use",
                ),
                AssistantMessage((Text("Current work finished"),)),
                AssistantMessage((Text("Summary"),)),
            ]
        )
        agent = Agent(provider, [define_tool("echo", "Echo", EchoArgs, execute)])
        assert (await agent.prompt("Begin")).reason == "complete"
        assert executed == ["first", "second"]
        assert provider.requests[1].messages[-1] == UserMessage("Change direction")
        assert [m.role for m in provider.requests[1].messages[-3:]] == [
            "tool_result",
            "tool_result",
            "user",
        ]
        assert provider.requests[2].messages[-1] == UserMessage("Summarize afterward")
        assert agent.queued_messages == (0, 0)

    asyncio.run(case())


def test_queues_drain_one_message_per_checkpoint() -> None:
    async def case() -> None:
        provider = ScriptedProvider([AssistantMessage((Text(str(i)),)) for i in range(4)])
        agent = Agent(provider)
        agent.steer("s1")
        agent.steer("s2")
        agent.follow_up("f1")
        agent.follow_up("f2")
        assert (await agent.prompt("Start")).reason == "complete"
        assert [request.messages[-1] for request in provider.requests] == [
            UserMessage("s1"),
            UserMessage("s2"),
            UserMessage("f1"),
            UserMessage("f2"),
        ]

    asyncio.run(case())


class WaitingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
        try:
            self.started.set()
            yield TextDelta("unfinished")
            await asyncio.Event().wait()
        finally:
            self.closed = True


def test_cancel_closes_stream_rolls_back_turn_and_releases_agent() -> None:
    async def case() -> None:
        provider = WaitingProvider()
        agent = Agent(provider)
        original = agent.messages
        task = agent.start("Wait")
        await provider.started.wait()
        agent.cancel()
        result = await task
        assert result.reason == "cancelled"
        assert provider.closed and not agent.is_running
        assert agent.messages == original

    asyncio.run(case())


def test_immediate_cancellation_and_concurrent_start_guard() -> None:
    async def case() -> None:
        provider = ScriptedProvider([AssistantMessage((Text("next"),))])
        agent = Agent(provider)
        task = agent.start("first")
        with pytest.raises(RuntimeError, match="already running"):
            agent.start("second")
        agent.cancel()
        assert (await task).reason == "cancelled"
        assert not agent.is_running and not provider.requests
        assert (await agent.prompt("next")).reason == "complete"

    asyncio.run(case())


def test_cancel_during_tool_does_not_start_later_tools() -> None:
    async def case() -> None:
        started = asyncio.Event()
        executed: list[str] = []

        async def execute(args: EchoArgs) -> str:
            executed.append(args.text)
            started.set()
            await asyncio.Event().wait()
            return "unreachable"

        provider = ScriptedProvider(
            [
                AssistantMessage(
                    (
                        ToolCall("c1", "echo", {"text": "one"}),
                        ToolCall("c2", "echo", {"text": "two"}),
                    ),
                    "tool_use",
                )
            ]
        )
        agent = Agent(provider, [define_tool("echo", "Echo", EchoArgs, execute)])
        task = agent.start("Run tools")
        await started.wait()
        agent.cancel()
        assert (await task).reason == "cancelled"
        assert executed == ["one"]
        assert len(agent.messages) == 1

    asyncio.run(case())


def test_failed_turn_restores_selected_queue_input_and_keeps_completed_turn() -> None:
    async def case() -> None:
        provider = ScriptedProvider(
            [AssistantMessage((Text("First complete"),)), RuntimeError("network failed")]
        )
        agent = Agent(provider)
        agent.follow_up("Later")
        result = await agent.prompt("Begin")
        assert result.reason == "error" and result.error == "network failed"
        assert agent.messages[-1] == AssistantMessage((Text("First complete"),))
        assert agent.queued_messages == (0, 1)
        assert not agent.is_running

    asyncio.run(case())


def test_truncated_response_never_executes_tools() -> None:
    async def case() -> None:
        calls: list[str] = []
        provider = ScriptedProvider(
            [AssistantMessage((ToolCall("c", "echo", {"text": "unsafe"}),), "length")]
        )
        agent = Agent(provider, [echo_tool(calls)])
        assert (await agent.prompt("Try")).reason == "length"
        assert not calls and len(provider.requests) == 1
        result = agent.messages[-1]
        assert isinstance(result, ToolResult) and result.is_error

    asyncio.run(case())


def test_turn_limit_stops_repeated_tool_requests() -> None:
    async def case() -> None:
        calls: list[str] = []
        provider = ScriptedProvider(
            [AssistantMessage((ToolCall("c", "echo", {"text": "one"}),), "tool_use")]
        )
        agent = Agent(provider, [echo_tool(calls)], max_turns=1)
        agent.follow_up("Keep this queued")
        assert (await agent.prompt("Begin")).reason == "turn_limit"
        assert calls == ["one"] and len(provider.requests) == 1
        assert agent.queued_messages == (0, 1)

    asyncio.run(case())


@pytest.mark.parametrize("timeout", [False, True])
def test_tool_exceptions_and_timeouts_become_results(timeout: bool) -> None:
    async def case() -> None:
        async def execute(args: EchoArgs) -> str:
            if timeout:
                await asyncio.Event().wait()
            raise OSError("Disk unavailable")

        provider = ScriptedProvider(
            [
                AssistantMessage((ToolCall("c", "echo", {"text": "x"}),), "tool_use"),
                AssistantMessage((Text("Handled"),)),
            ]
        )
        agent = Agent(provider, [define_tool("echo", "Echo", EchoArgs, execute)], tool_timeout=0.01)
        assert (await agent.prompt("Begin")).reason == "complete"
        result = provider.requests[1].messages[-1]
        assert isinstance(result, ToolResult) and result.is_error
        assert ("timed out" if timeout else "Disk unavailable") in result.content

    asyncio.run(case())


def test_duplicate_tool_ids_are_rejected_before_execution() -> None:
    async def case() -> None:
        calls: list[str] = []
        call = ToolCall("same", "echo", {"text": "x"})
        agent = Agent(
            ScriptedProvider([AssistantMessage((call, call), "tool_use")]), [echo_tool(calls)]
        )
        assert (await agent.prompt("Begin")).reason == "error"
        assert not calls and len(agent.messages) == 1

    asyncio.run(case())


def test_agent_remains_running_until_end_listener_settles() -> None:
    async def case() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        agent = Agent(ScriptedProvider([AssistantMessage((Text("Done"),))]))

        async def observe(event: AgentEvent) -> None:
            if event.type == "run_end":
                started.set()
                await release.wait()

        agent.subscribe(observe)
        task = agent.start("Begin")
        await started.wait()
        assert agent.is_running and not task.done()
        release.set()
        assert (await task).reason == "complete"
        assert not agent.is_running

    asyncio.run(case())


@pytest.mark.parametrize("mode", ["missing_final", "events_after_final", "wrong_stop_reason"])
def test_broken_provider_protocol_never_executes_tools(mode: str) -> None:
    async def case() -> None:
        calls: list[str] = []

        class BrokenProvider:
            async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
                yield TextDelta("partial")
                if mode == "missing_final":
                    return
                yield ResponseCompleted(
                    AssistantMessage(
                        (ToolCall("c", "echo", {"text": "x"}),),
                        "stop" if mode == "wrong_stop_reason" else "tool_use",
                    )
                )
                if mode == "events_after_final":
                    yield TextDelta("too late")

        agent = Agent(BrokenProvider(), [echo_tool(calls)])
        assert (await agent.prompt("Begin")).reason == "error"
        assert not calls and len(agent.messages) == 1

    asyncio.run(case())
