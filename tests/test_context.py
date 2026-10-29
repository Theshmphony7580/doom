from dataclasses import replace

import pytest

from pi_in_py.agent.agent import Agent
from pi_in_py.agent.compaction import Compaction
from pi_in_py.ai.context import estimate_context, estimate_message, format_context
from pi_in_py.ai.fake import ScriptedProvider
from pi_in_py.ai.models import resolve_context_window
from pi_in_py.ai.types import (
    AssistantMessage,
    Request,
    SystemMessage,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    UserMessage,
)


def test_latest_usage_includes_cache_once_and_only_estimates_trailing_messages() -> None:
    latest = AssistantMessage((Text("new"),), usage=Usage(100, 20, 30, 40))
    result = ToolResult("c", "read", "result")
    messages = (
        SystemMessage("system"),
        AssistantMessage((Text("old"),), usage=Usage(9999)),
        latest,
        result,
    )
    assert latest.usage.total_tokens == 190
    assert estimate_context(messages) == (190 + estimate_message(result), True)
    assert estimate_context(messages[:-1]) == (190, False)
    assert estimate_context(messages, usage_start=len(messages))[0] < 190


def test_estimates_cover_system_tools_calls_results_and_zero_usage() -> None:
    messages = (
        SystemMessage("system instructions"),
        UserMessage("question"),
        AssistantMessage((Text("read"), ToolCall("c", "read", {"path": "a"})), "tool_use"),
        ToolResult("c", "read", "contents"),
    )
    tools = (ToolSpec("read", "Read a file", {"type": "object"}),)
    tokens, estimated = estimate_context(messages, tools)
    assert estimated
    assert tokens > estimate_context(messages)[0]
    assert tokens > estimate_context(messages[1:], tools)[0]
    assert estimate_context((*messages, UserMessage("x" * 400)), tools)[0] == tokens + 104


@pytest.mark.parametrize("provider", ["groq", "anthropic"])
def test_capacity_override_precedence(provider: str) -> None:
    environ = {f"{provider.upper()}_CONTEXT_WINDOW": "1234", "OTHER_CONTEXT_WINDOW": "99"}
    assert resolve_context_window(provider, "unknown", 5678, environ) == 5678
    assert resolve_context_window(provider, "unknown", None, environ) == 1234
    assert resolve_context_window(provider, "unknown", None, {}) is None
    # An invalid lower-precedence override cannot defeat a valid CLI override.
    assert (
        resolve_context_window(
            provider, "unknown", 50, {**environ, f"{provider.upper()}_CONTEXT_WINDOW": "bad"}
        )
        == 50
    )


@pytest.mark.parametrize("value", ["", "bad", "0", "-1", "1.2"])
def test_invalid_capacity_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        resolve_context_window("groq", "unknown", None, {"GROQ_CONTEXT_WINDOW": value})


def test_catalog_and_unknown_capacity_display() -> None:
    assert resolve_context_window("groq", "qwen/qwen3.8-27b", None, {}) == 131072
    assert resolve_context_window("anthropic", "claude-haiku-4-5-20251001", None, {}) == 200000
    assert resolve_context_window("anthropic", "claude-sonnet-5-5", None, {}) == 1000000
    agent = Agent(ScriptedProvider([]))
    usage = agent.get_context_usage()
    assert usage.tokens is not None and usage.estimated and not usage.auto_compact
    assert usage.context_window is None and usage.percent is None
    assert "/?" in format_context(usage)
    assert "auto off" in format_context(usage)


def test_resume_and_compaction_do_not_trust_old_usage() -> None:
    messages = (
        SystemMessage("system"),
        UserMessage("old"),
        AssistantMessage((Text("old"),), usage=Usage(900000)),
        UserMessage("recent"),
        AssistantMessage((Text("recent"),), usage=Usage(800000)),
    )
    resumed = Agent(ScriptedProvider([]), messages=messages, context_window=200000)
    assert resumed.get_context_usage().estimated_tokens < 100
    compacted = Agent(
        ScriptedProvider([]),
        messages=messages,
        context_window=200000,
        compaction=Compaction("summary", 3, len(messages)),
    )
    usage = compacted.get_context_usage()
    assert usage.tokens is None and usage.percent is None
    assert usage.estimated_tokens < 100
    assert "context=?/200,000" in format_context(usage)
    assert "90.1%" in format_context(replace(usage, tokens=180200, percent=90.1))


def test_output_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Request((UserMessage("hi"),), (), max_output_tokens=0)
