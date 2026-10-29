import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncGenerator
from io import StringIO
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter

from evals.run import Case, create_workspace, evaluate_case
from pi_in_py.agent.agent import TOOL_LIMIT_NOTICE, Agent
from pi_in_py.agent.compaction import SUMMARY_PROMPT
from pi_in_py.agent.types import define_tool
from pi_in_py.ai.context import estimate_context
from pi_in_py.ai.fake import ScriptedProvider
from pi_in_py.ai.providers.groq import GroqAuth
from pi_in_py.ai.types import (
    AssistantMessage,
    ProviderEvent,
    Request,
    ResponseCompleted,
    SystemMessage,
    Text,
    ToolCall,
    ToolResult,
    UserMessage,
)
from pi_in_py.app import cli, prompt
from pi_in_py.app.auth_store import FileCredentialStore
from pi_in_py.app.session import SessionStore, validate_transcript
from pi_in_py.app.ui_protocol import run_ui_protocol


class NoArgs(BaseModel):
    pass


def demo_args(workspace: Path) -> argparse.Namespace:
    return argparse.Namespace(
        provider="anthropic",
        model=None,
        demo=True,
        workspace=str(workspace),
        session=str(workspace / "session.jsonl"),
        max_turns=20,
        max_tool_calls=20,
        trace=False,
        prompt=None,
    )


def test_packaged_prompt_loads_outside_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    content = prompt.load_system_prompt()
    assert 0 < len(content.split()) < 180
    assert len(prompt.prompt_hash(content)) == 64


@pytest.mark.parametrize("content", [None, " \n\t"])
def test_missing_or_empty_prompt_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
) -> None:
    monkeypatch.setattr(prompt, "files", lambda package: tmp_path)
    if content is not None:
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "system.md").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="system prompt"):
        prompt.load_system_prompt()


def test_prompt_revisions_preserve_history_and_order(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "session.jsonl", tmp_path)
    original = (SystemMessage("original"), UserMessage("hi"), AssistantMessage((Text("hi"),)))
    store.append_turn(original)  # A legacy session with no prompt record.
    prefix = store.path.read_bytes()
    assert store.append_prompt("first revision")
    assert not store.append_prompt("first revision")
    store.append_turn((*original, UserMessage("again"), AssistantMessage((Text("answer"),))))
    assert store.append_prompt("second revision")
    loaded = SessionStore(store.path, tmp_path)
    assert store.path.read_bytes().startswith(prefix)
    assert loaded.messages == store.messages
    assert loaded.messages[: len(original)] == original
    assert loaded.prompt == store.prompt
    assert loaded.prompt is not None and loaded.prompt.content == "second revision"
    records = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
    assert [r["type"] for r in records] == ["header", "turn", "prompt", "turn", "prompt"]


@pytest.mark.parametrize("content,digest", [("", "0" * 64), ("changed", "0" * 64)])
def test_invalid_prompt_records_are_rejected(tmp_path: Path, content: str, digest: str) -> None:
    store = SessionStore(tmp_path / "s.jsonl", tmp_path)
    with store.path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"type": "prompt", "content": content, "sha256": digest}) + "\n")
    with pytest.raises(ValueError, match="line 2"):
        SessionStore(store.path, tmp_path)


def test_prompt_only_session_roundtrips(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.jsonl", tmp_path)
    store.append_prompt("current")
    loaded = SessionStore(store.path, tmp_path)
    assert loaded.prompt == store.prompt and not loaded.messages


def test_failed_prompt_persistence_prevents_model_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionStore(tmp_path / "session.jsonl", tmp_path)
    store.append_prompt("previous")
    original = store.path.read_bytes()

    def fail(_: int) -> None:
        raise OSError("disk failure")

    def forbidden_provider(*args: object, **kwargs: object) -> None:
        raise AssertionError("Provider must not be constructed")

    monkeypatch.setattr("pi_in_py.app.session.os.fsync", fail)
    monkeypatch.setattr(cli, "ScriptedProvider", forbidden_provider)
    with pytest.raises(OSError, match="disk failure"):
        asyncio.run(cli.run(demo_args(tmp_path)))
    assert store.path.read_bytes() == original
    assert SessionStore(store.path, tmp_path).prompt == store.prompt


def test_cli_resume_uses_current_prompt_and_preserves_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "README.md").write_text("demo", encoding="utf-8")
    store = SessionStore(tmp_path / "session.jsonl", tmp_path)
    original = (
        SystemMessage("old instructions"),
        UserMessage("hi"),
        AssistantMessage((Text("hi"),)),
    )
    store.append_turn(original)
    prefix = store.path.read_bytes()
    provider = ScriptedProvider([AssistantMessage((Text("answer"),))])
    monkeypatch.setattr(cli, "ScriptedProvider", lambda responses: provider)
    args = demo_args(tmp_path)
    args.trace = True
    assert asyncio.run(cli.run(args)) == 0
    assert provider.requests[0].messages[0] == SystemMessage(prompt.load_system_prompt())
    loaded = SessionStore(store.path, tmp_path)
    assert loaded.messages[: len(original)] == original
    assert store.path.read_bytes().startswith(prefix)
    output = capsys.readouterr().err
    assert "current Doom instructions" in output
    assert prompt.prompt_hash(prompt.load_system_prompt()) in output


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_tool_budget_precedes_session_creation(tmp_path: Path, limit: int) -> None:
    args = demo_args(tmp_path)
    args.max_tool_calls = limit
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(cli.run(args))
    assert not (tmp_path / "session.jsonl").exists()
    with pytest.raises(ValueError, match="positive"):
        Agent(ScriptedProvider([]), max_tool_calls=limit)


@pytest.mark.parametrize("override", [None, "new instructions"])
def test_request_prompt_override_does_not_rewrite_transcript(override: str | None) -> None:
    async def case() -> None:
        original = (
            SystemMessage("saved instructions"),
            UserMessage("hi"),
            AssistantMessage((Text("hi"),)),
        )
        provider = ScriptedProvider([AssistantMessage((Text("answer"),))])
        agent = Agent(provider, messages=original, system_prompt=override)
        assert (await agent.prompt("next")).reason == "complete"
        assert agent.messages[: len(original)] == original
        assert provider.requests[0].messages[0] == SystemMessage(override or "saved instructions")

    asyncio.run(case())


def test_budget_denies_batch_overflow_and_allows_final_answer() -> None:
    async def case() -> None:
        executed: list[str] = []

        async def execute(_: NoArgs) -> str:
            executed.append("read")
            return "evidence"

        provider = ScriptedProvider(
            [
                AssistantMessage(tuple(ToolCall(str(i), "read", {}) for i in range(4)), "tool_use"),
                AssistantMessage((Text("answer"),)),
            ]
        )
        agent = Agent(provider, [define_tool("read", "Read", NoArgs, execute)], max_tool_calls=2)
        assert (await agent.prompt("read")).reason == "complete"
        assert executed == ["read", "read"] and agent.tool_calls_used == 2
        results = [m for m in agent.messages if isinstance(m, ToolResult)]
        assert [r.is_error for r in results] == [False, False, True, True]
        assert not provider.requests[1].tools
        assert TOOL_LIMIT_NOTICE in str(provider.requests[1].messages[0])
        assert TOOL_LIMIT_NOTICE not in str(agent.messages[0])
        assert (
            agent.get_context_usage().estimated_tokens
            == estimate_context(agent.context_messages)[0]
        )
        validate_transcript(agent.messages)

    asyncio.run(case())


@pytest.mark.parametrize("failure", ["arguments", "unknown", "exception", "timeout"])
def test_failed_attempts_consume_budget(failure: str) -> None:
    async def case() -> None:
        executed: list[str] = []

        async def execute(_: NoArgs) -> str:
            executed.append("attempt")
            if failure == "timeout":
                await asyncio.Event().wait()
            raise OSError("disk failure")

        call = ToolCall("bad", "unknown" if failure == "unknown" else "read", {})
        if failure == "arguments":
            call = ToolCall("bad", "read", {"required": "missing"})

        class RequiredArgs(BaseModel):
            value: str

        async def required(_: RequiredArgs) -> str:
            raise AssertionError("Invalid arguments must not execute")

        tool = (
            define_tool("read", "Read", RequiredArgs, required)
            if failure == "arguments"
            else define_tool("read", "Read", NoArgs, execute)
        )
        provider = ScriptedProvider(
            [
                AssistantMessage((call,), "tool_use"),
                AssistantMessage((ToolCall("denied", "read", {}),), "tool_use"),
            ]
        )
        agent = Agent(provider, [tool], max_tool_calls=1, tool_timeout=0.01)
        assert (await agent.prompt("read")).reason == "tool_limit"
        assert agent.tool_calls_used == 1 and not provider.requests[1].tools
        assert len(executed) == (1 if failure in ("exception", "timeout") else 0)
        validate_transcript(agent.messages)

    asyncio.run(case())


def test_budget_is_shared_by_queues_and_resets_for_new_run() -> None:
    async def case() -> None:
        async def execute(_: NoArgs) -> str:
            agent.steer("steering")
            agent.follow_up("followup")
            return "evidence"

        provider = ScriptedProvider(
            [
                AssistantMessage((ToolCall("one", "read", {}),), "tool_use"),
                AssistantMessage((Text("answer"),)),
                AssistantMessage((Text("followup answer"),)),
                AssistantMessage((ToolCall("two", "read", {}),), "tool_use"),
                AssistantMessage((Text("next answer"),)),
                AssistantMessage((Text("next followup"),)),
            ]
        )
        agent = Agent(provider, [define_tool("read", "Read", NoArgs, execute)], max_tool_calls=1)
        assert (await agent.prompt("first")).reason == "complete"
        assert [bool(r.tools) for r in provider.requests] == [True, False, False]
        assert provider.requests[1].messages[-1] == UserMessage("steering")
        assert provider.requests[2].messages[-1] == UserMessage("followup")
        assert (await agent.prompt("second")).reason == "complete"
        assert provider.requests[3].tools and agent.tool_calls_used == 1

    asyncio.run(case())


def test_turn_limit_still_prevents_an_extra_final_request() -> None:
    async def case() -> None:
        async def execute(_: NoArgs) -> str:
            return "evidence"

        provider = ScriptedProvider([AssistantMessage((ToolCall("c", "read", {}),), "tool_use")])
        agent = Agent(
            provider, [define_tool("read", "Read", NoArgs, execute)], max_turns=1, max_tool_calls=1
        )
        assert (await agent.prompt("read")).reason == "turn_limit"
        assert len(provider.requests) == 1 and agent.tool_calls_used == 1

    asyncio.run(case())


def test_cancelled_attempt_counts_and_new_run_resets() -> None:
    async def case() -> None:
        started = asyncio.Event()

        async def execute(_: NoArgs) -> str:
            started.set()
            await asyncio.Event().wait()
            return "unreachable"

        provider = ScriptedProvider(
            [
                AssistantMessage(
                    (ToolCall("c", "read", {}), ToolCall("d", "read", {})), "tool_use"
                ),
                AssistantMessage((Text("hello"),)),
            ]
        )
        agent = Agent(provider, [define_tool("read", "Read", NoArgs, execute)], max_tool_calls=1)
        task = agent.start("read")
        await asyncio.wait_for(started.wait(), timeout=5)
        agent.cancel()
        assert (await task).reason == "cancelled"
        assert agent.tool_calls_used == 1 and len(agent.messages) == 1
        assert (await agent.prompt("hi")).reason == "complete"
        assert agent.tool_calls_used == 0

    asyncio.run(case())


def test_status_shows_prompt_hash_and_budget(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO("/status\n/quit\n"))
    agent = Agent(ScriptedProvider([]), system_prompt="current", max_tool_calls=3)
    assert asyncio.run(cli.interactive(agent, GroqAuth(FileCredentialStore()))) == 0
    output = capsys.readouterr().err
    assert "tools=0/3" in output and prompt.prompt_hash("current") in output


def test_current_prompt_survives_compaction_and_resume(tmp_path: Path) -> None:
    async def case() -> None:
        requests: list[Request] = []

        class SummaryProvider:
            async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
                requests.append(request)
                yield ResponseCompleted(AssistantMessage((Text("Concise answer or summary"),)))

        original = (
            SystemMessage("old instructions"),
            UserMessage("x" * 5000),
            AssistantMessage((Text("y" * 5000),)),
            UserMessage("recent question"),
            AssistantMessage((Text("recent answer"),)),
        )
        store = SessionStore(tmp_path / "s.jsonl", tmp_path)
        store.append_turn(original)
        prefix = store.path.read_bytes()
        store.append_prompt("current instructions")
        agent = Agent(
            SummaryProvider(),
            messages=original,
            system_prompt="current instructions",
            context_window=2048,
            save_compaction=store.append_compaction,
        )
        assert (await agent.prompt("next")).reason == "complete"
        store.append_turn(agent.messages)
        assert agent.compaction is not None
        normal = [r for r in requests if r.messages[0] != SystemMessage(SUMMARY_PROMPT)]
        assert len(normal) == 1 and normal[0].messages[0] == SystemMessage("current instructions")
        assert agent.messages[: len(original)] == original
        assert store.path.read_bytes().startswith(prefix)
        loaded = SessionStore(store.path, tmp_path)
        resumed = Agent(
            SummaryProvider(),
            messages=loaded.messages,
            system_prompt="current instructions",
            context_window=2048,
            compaction=loaded.compaction,
        )
        assert resumed.context_messages == agent.context_messages
        assert (
            resumed.get_context_usage().estimated_tokens
            == estimate_context(
                resumed.context_messages,
            )[0]
        )

    asyncio.run(case())


def test_evaluation_requires_explicit_live_option(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.run",
            "--provider",
            "groq",
            "--model",
            "fake",
            "--output",
            str(tmp_path / "results.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2 and "--live" in result.stderr
    assert not (tmp_path / "results.json").exists()


def test_evaluation_fixtures_and_reporting(tmp_path: Path) -> None:
    cases_path = Path(__file__).parents[1] / "evals" / "cases.json"
    cases = TypeAdapter(list[Case]).validate_json(cases_path.read_text(encoding="utf-8"))
    assert len(cases) == len({case.id for case in cases}) == 14

    async def run() -> None:
        for case in cases:
            root = tmp_path / case.id
            root.mkdir()
            create_workspace(root, case)
            provider = ScriptedProvider([AssistantMessage((Text("answer"),)) for _ in case.prompts])
            report = await evaluate_case(provider, case, "identity", root)
            assert report["human_review"] == "pending"
            assert report["prompt_sha256"] == prompt.prompt_hash("identity")
            assert len(provider.requests) == len(case.prompts)

    asyncio.run(run())


def test_protocol_reports_budget_prompt_hash_and_update_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO('{"type":"submit","text":"/status"}\n'))
    agent = Agent(ScriptedProvider([]), system_prompt="current", max_tool_calls=7)
    store = SessionStore(tmp_path / "s.jsonl", tmp_path)
    store.append_prompt("current")
    assert (
        asyncio.run(
            run_ui_protocol(
                agent,
                GroqAuth(FileCredentialStore()),
                store,
                "groq",
                "fake",
                False,
                None,
                False,
                prompt_updated=True,
            )
        )
        == 0
    )
    packets = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert packets[0]["type"] == "ready"
    notices = [packet["text"] for packet in packets if packet["type"] == "notice"]
    assert any("current Doom instructions" in text for text in notices)
    assert any("tools=0/7" in text and prompt.prompt_hash("current") in text for text in notices)


def test_cli_budget_flag_denies_extra_demo_call(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("must not be read", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pi_in_py",
            "--demo",
            "--workspace",
            str(tmp_path),
            "--session",
            str(tmp_path / "s.jsonl"),
            "--max-tool-calls",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    messages = SessionStore(tmp_path / "s.jsonl", tmp_path).messages
    results = [m for m in messages if isinstance(m, ToolResult)]
    assert len(results) == 2 and not results[0].is_error and results[1].is_error
    assert "budget exhausted" in results[1].content
    assert "must not be read" not in str(messages)
