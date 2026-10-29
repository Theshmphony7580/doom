import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import JsonValue, TypeAdapter

from pi_in_py.agent.agent import Agent
from pi_in_py.agent.types import AgentEvent
from pi_in_py.ai.openai import OpenAIChatProvider, to_messages
from pi_in_py.ai.types import (
    AssistantMessage,
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
from pi_in_py.app.tools import filesystem_tools


def chunk(delta: dict[str, JsonValue], reason: str | None = None) -> dict[str, JsonValue]:
    return {
        "id": "completion-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
    }


def sse(chunks: list[dict[str, JsonValue]]) -> bytes:
    return (
        "".join(f"data: {json.dumps(item)}\n\n" for item in chunks) + "data: [DONE]\n\n"
    ).encode()


def client_for(handle: Callable[[httpx2.Request], httpx2.Response]) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="fake-groq-key",
        base_url="https://api.groq.com/openai/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    )


@pytest.mark.parametrize("output_limit", [None, 123])
def test_streams_text_usage_and_uses_groq_endpoint_and_key(output_limit: int | None) -> None:
    async def case() -> None:
        captured: list[dict[str, JsonValue]] = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer fake-groq-key"
            payload = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
            captured.append(payload)
            usage_chunk = chunk({})
            usage_chunk["choices"] = []
            usage_chunk["usage"] = {
                "prompt_tokens": 5,
                "completion_tokens": 8,
                "total_tokens": 13,
                "prompt_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 1},
            }
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    [
                        chunk({"content": "Hel"}),
                        chunk({"content": "lo"}),
                        chunk({}, "stop"),
                        usage_chunk,
                    ]
                ),
            )

        async with client_for(handle) as client:
            events = [
                event
                async for event in OpenAIChatProvider(client, "test-model").stream(
                    Request((SystemMessage("System"), UserMessage("Hi")), (), output_limit)
                )
            ]
        assert events[:2] == [TextDelta("Hel"), TextDelta("lo")]
        assert events[-1] == ResponseCompleted(
            AssistantMessage((Text("Hello"),), usage=Usage(2, 8, 2, 1))
        )
        assert captured[0]["stream_options"] == {"include_usage": True}
        assert captured[0]["max_completion_tokens"] == (output_limit or 4096)
        assert "tools" not in captured[0]

    asyncio.run(case())


def test_fragmented_interleaved_tool_calls_complete_before_execution(tmp_path: Path) -> None:
    async def case() -> None:
        (tmp_path / "notes.txt").write_text("File contents", encoding="utf-8")
        captured: list[dict[str, JsonValue]] = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            captured.append(TypeAdapter(dict[str, JsonValue]).validate_json(request.content))
            if len(captured) == 1:
                frames = [
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "id": "c2",
                                    "type": "function",
                                    "function": {"name": "list_", "arguments": '{"path":'},
                                }
                            ]
                        }
                    ),
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "read_", "arguments": '{"path":"notes'},
                                }
                            ]
                        }
                    ),
                    chunk(
                        {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "file", "arguments": '.txt"}'}},
                                {"index": 1, "function": {"name": "files", "arguments": '"."}'}},
                            ]
                        }
                    ),
                    chunk({}, "tool_calls"),
                ]
            else:
                frames = [chunk({"content": "Summary"}), chunk({}, "stop")]
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content=sse(frames)
            )

        async with client_for(handle) as client:
            agent = Agent(OpenAIChatProvider(client, "test-model"), filesystem_tools(tmp_path))
            assert (await agent.prompt("Read and list")).reason == "complete"
        assert len(captured) == 2 and isinstance(captured[0]["tools"], list)
        assert agent.messages[2] == AssistantMessage(
            (
                ToolCall("c1", "read_file", {"path": "notes.txt"}),
                ToolCall("c2", "list_files", {"path": "."}),
            ),
            "tool_use",
        )
        assert agent.messages[3] == ToolResult("c1", "read_file", "File contents")
        second = captured[1]["messages"]
        assert isinstance(second, list)
        assert second[-2] == {"role": "tool", "tool_call_id": "c1", "content": "File contents"}
        assert second[-1] == {"role": "tool", "tool_call_id": "c2", "content": "notes.txt"}

    asyncio.run(case())


@pytest.mark.parametrize("arguments", ['{"path":', "[]", "null"])
def test_invalid_tool_json_fails_without_executing(arguments: str) -> None:
    async def case() -> None:
        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    [
                        chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c",
                                        "type": "function",
                                        "function": {"name": "read_file", "arguments": arguments},
                                    }
                                ]
                            }
                        ),
                        chunk({}, "tool_calls"),
                    ]
                ),
            )

        async with client_for(handle) as client:
            agent = Agent(OpenAIChatProvider(client, "test-model"))
            result = await agent.prompt("Read")
        assert result.reason == "error" and result.error is not None
        assert "Invalid JSON" in result.error and len(agent.messages) == 1

    asyncio.run(case())


def test_truncated_arguments_are_not_parsed_or_executed() -> None:
    async def case() -> None:
        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    [
                        chunk(
                            {
                                "content": "Partial",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c",
                                        "function": {"name": "read_file", "arguments": '{"path":'},
                                    }
                                ],
                            }
                        ),
                        chunk({}, "length"),
                    ]
                ),
            )

        events: list[AgentEvent] = []

        async def observe(event: AgentEvent) -> None:
            events.append(event)

        async with client_for(handle) as client:
            agent = Agent(OpenAIChatProvider(client, "test-model"))
            agent.subscribe(observe)
            assert (await agent.prompt("Read")).reason == "length"
        assert agent.messages[-1] == AssistantMessage((Text("Partial"),), "length")
        assert not any(event.type == "tool_start" for event in events)

    asyncio.run(case())


@pytest.mark.parametrize("finish", [None, "content_filter", "function_call"])
def test_missing_and_unsupported_finish_reasons_fail(finish: str | None) -> None:
    async def case() -> None:
        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    [
                        chunk({"content": "Partial"}, finish),
                    ]
                ),
            )

        async with client_for(handle) as client:
            agent = Agent(OpenAIChatProvider(client, "test-model"))
            result = await agent.prompt("Hi")
        assert result.reason == "error" and len(agent.messages) == 1
        assert result.error is not None and "finish reason" in result.error

    asyncio.run(case())


@pytest.mark.parametrize("status", [401, 429])
def test_http_errors_become_failed_runs(status: int) -> None:
    async def case() -> None:
        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(status, json={"error": {"message": "Test error"}})

        async with client_for(handle) as client:
            agent = Agent(OpenAIChatProvider(client, "test-model"))
            result = await agent.prompt("Hi")
        assert result.reason == "error" and result.error is not None
        assert str(status) in result.error and not agent.is_running

    asyncio.run(case())


def test_cancellation_closes_the_sdk_http_stream() -> None:
    async def case() -> None:
        started = asyncio.Event()

        class PausingStream(httpx2.AsyncByteStream):
            closed = False

            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield f"data: {json.dumps(chunk({'content': 'Partial'}))}\n\n".encode()
                started.set()
                await asyncio.Event().wait()

            async def aclose(self) -> None:
                self.closed = True

        body = PausingStream()

        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

        async with client_for(handle) as client:
            agent = Agent(OpenAIChatProvider(client, "test-model"))
            task = agent.start("Wait")
            await started.wait()
            agent.cancel()
            assert (await task).reason == "cancelled"
        assert body.closed and len(agent.messages) == 1

    asyncio.run(case())


def test_native_messages_preserve_calls_and_tool_error_status() -> None:
    messages = to_messages(
        Request(
            (
                SystemMessage("Instructions"),
                UserMessage("Read"),
                AssistantMessage((ToolCall("c", "read", {"path": "a"}),), "tool_use"),
                ToolResult("c", "read", "Missing file", is_error=True),
            ),
            (),
        )
    )
    assert messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "type": "function",
                "id": "c",
                "function": {
                    "name": "read",
                    "arguments": '{"path": "a"}',
                },
            }
        ],
    }
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "c",
        "content": "Tool error: Missing file",
    }
