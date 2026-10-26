import asyncio
import json
from collections.abc import Sequence

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import JsonValue, TypeAdapter

from pi_in_py.agent.agent import Agent
from pi_in_py.ai.adapter import AnthropicProvider, to_messages
from pi_in_py.ai.types import (
    AssistantMessage,
    ProviderError,
    Request,
    ResponseCompleted,
    SystemMessage,
    Text,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    UserMessage,
)


def response_stream(tool: bool = False, stop_reason: str | None = None) -> bytes:
    block: dict[str, object]
    delta: dict[str, object]
    if tool:
        block = {"type": "tool_use", "id": "call_123", "name": "missing", "input": {}}
        delta = {"type": "input_json_delta", "partial_json": '{"path":"notes.txt"}'}
    else:
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": "Hello"}
    events: Sequence[dict[str, object]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "test-model",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 3,
                },
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason or ("tool_use" if tool else "end_turn"),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": 8},
        },
        {"type": "message_stop"},
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def test_adapter_streams_text_and_normalizes_usage() -> None:
    async def case() -> None:
        captured: list[dict[str, JsonValue]] = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            captured.append(TypeAdapter(dict[str, JsonValue]).validate_json(request.content))
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content=response_stream()
            )

        async with AsyncAnthropic(
            api_key="fake-test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            provider = AnthropicProvider(client, "test-model")
            events = [
                event
                async for event in provider.stream(
                    Request(
                        (SystemMessage("System"), UserMessage("Hi")),
                        (ToolSpec("read", "Read a file", {"type": "object"}),),
                    )
                )
            ]
        assert events[0] == TextDelta("Hello")
        assert events[-1] == ResponseCompleted(
            AssistantMessage((Text("Hello"),), usage=Usage(5, 8, 2, 3))
        )
        assert captured[0]["model"] == "test-model"
        assert captured[0]["system"] == "System"
        assert captured[0]["stream"] is True

    asyncio.run(case())


def test_adapter_tool_call_roundtrip_through_real_sdk() -> None:
    async def case() -> None:
        captured: list[dict[str, JsonValue]] = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            captured.append(TypeAdapter(dict[str, JsonValue]).validate_json(request.content))
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=response_stream(tool=len(captured) == 1),
            )

        async with AsyncAnthropic(
            api_key="fake-test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            agent = Agent(AnthropicProvider(client, "test-model"))
            assert (await agent.prompt("Read notes")).reason == "complete"
        assert len(captured) == 2
        assert agent.messages[2] == AssistantMessage(
            (ToolCall("call_123", "missing", {"path": "notes.txt"}),),
            "tool_use",
            Usage(5, 8, 2, 3),
        )
        payload = json.dumps(captured[1])
        assert '"tool_use_id": "call_123"' in payload
        assert '"is_error": true' in payload

    asyncio.run(case())


def test_provider_failure_becomes_failed_run() -> None:
    async def case() -> None:
        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                401,
                json={
                    "type": "error",
                    "error": {
                        "type": "authentication_error",
                        "message": "Invalid test credentials",
                    },
                },
            )

        async with AsyncAnthropic(
            api_key="fake-test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            agent = Agent(AnthropicProvider(client, "test-model"))
            result = await agent.prompt("Hello")
        assert result.reason == "error"
        assert result.error is not None and "401" in result.error
        assert len(agent.messages) == 1 and not agent.is_running

    asyncio.run(case())


def test_unknown_stop_reason_is_rejected() -> None:
    async def case() -> None:
        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=response_stream(stop_reason="pause_turn"),
            )

        async with AsyncAnthropic(
            api_key="fake-test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            with pytest.raises(ProviderError, match="stop reason"):
                _ = [
                    event
                    async for event in AnthropicProvider(client, "test-model").stream(
                        Request((UserMessage("Hi"),), ())
                    )
                ]

    asyncio.run(case())


def test_native_message_translation_preserves_tool_ids_and_errors() -> None:
    system, messages = to_messages(
        Request(
            (
                SystemMessage("Instructions"),
                UserMessage("Read"),
                AssistantMessage(
                    (Text("Reading"), ToolCall("c", "read", {"path": "a"})), "tool_use"
                ),
                ToolResult("c", "read", "Missing file", is_error=True),
            ),
            (),
        )
    )
    assert system == "Instructions"
    assert messages[-1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "c", "content": "Missing file", "is_error": True}
        ],
    }
