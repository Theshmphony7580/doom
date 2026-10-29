import argparse
import asyncio
import getpass
import json
import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from io import StringIO
from pathlib import Path

import httpx2
import pytest
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import JsonValue, TypeAdapter

from pi_in_py.agent.agent import Agent
from pi_in_py.ai.auth import ApiKeyCredential
from pi_in_py.ai.fake import ScriptedProvider
from pi_in_py.ai.providers.groq import GroqAuth
from pi_in_py.ai.types import (
    AssistantMessage,
    ProviderEvent,
    Request,
    ResponseCompleted,
    Text,
    UserMessage,
)
from pi_in_py.app import cli
from pi_in_py.app.auth_store import FileCredentialStore
from pi_in_py.app.cli import interactive
from pi_in_py.app.session import SessionStore


@pytest.mark.parametrize("provider", ["anthropic", "groq"])
def test_offline_cli_demo_and_trace(tmp_path: Path, provider: str) -> None:
    (tmp_path / "README.md").write_text("Demo input", encoding="utf-8")
    path = tmp_path / "demo.jsonl"
    environment = os.environ.copy()
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("GROQ_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pi_in_py",
            "--demo",
            "--provider",
            provider,
            "--trace",
            "--workspace",
            str(tmp_path),
            "--session",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Offline demo finished" in result.stdout
    assert '"type": "turn_end"' in result.stderr
    assert [m.role for m in SessionStore(path, tmp_path).messages] == [
        "system",
        "user",
        "assistant",
        "tool_result",
        "tool_result",
        "assistant",
    ]


def test_interactive_accepts_followups_and_finishes_on_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO("Hello\n/followup Then summarize\n"))
    provider = ScriptedProvider(
        [AssistantMessage((Text("First"),)), AssistantMessage((Text("Second"),))]
    )
    agent = Agent(provider)
    assert asyncio.run(interactive(agent, GroqAuth(FileCredentialStore()))) == 0
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[-1] == UserMessage("Then summarize")


@pytest.mark.parametrize("provider", ["anthropic", "groq"])
def test_missing_configuration_fails_before_creating_session(tmp_path: Path, provider: str) -> None:
    environment = os.environ.copy()
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("ANTHROPIC_MODEL", None)
    environment.pop("GROQ_MODEL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pi_in_py",
            "--provider",
            provider,
            "--workspace",
            str(tmp_path),
            "--prompt",
            "Hi",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
        check=False,
    )
    assert result.returncode == 1
    assert f"{provider.upper()}_MODEL" in result.stderr
    assert not (tmp_path / ".pi-in-py").exists()


def test_standalone_auth_commands_do_not_require_model_or_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def enter_key(prompt: str, stream: object = None) -> str:
        return "fake-secret-key"

    monkeypatch.setattr(getpass, "getpass", enter_key)
    for action in ("login", "status", "logout"):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pi-in-py",
                "--workspace",
                str(tmp_path / "does-not-exist"),
                "auth",
                action,
                "groq",
            ],
        )
        with pytest.raises(SystemExit) as result:
            cli.main()
        assert result.value.code == 0
    output = capsys.readouterr()
    assert "configured via stored credential" in output.err
    assert "Groq: not configured" in output.err
    assert "fake-secret-key" not in output.out + output.err
    assert not list(tmp_path.rglob("*.jsonl"))


def test_interactive_login_status_and_logout_do_not_enter_conversation_or_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/login groq\n/auth-status groq\n/logout groq\n/quit\n",
        ),
    )

    def enter_key(prompt: str, stream: object = None) -> str:
        return "fake-secret-key"

    monkeypatch.setattr(getpass, "getpass", enter_key)
    provider = ScriptedProvider([])
    agent = Agent(provider)
    store = SessionStore(tmp_path / "session.jsonl", tmp_path)
    agent.subscribe(cli.Renderer(store, trace=True))
    assert asyncio.run(interactive(agent, GroqAuth(FileCredentialStore()))) == 0
    assert not provider.requests
    assert len(agent.messages) == 1
    assert not store.messages
    output = capsys.readouterr()
    assert "configured via stored credential" in output.err
    assert "Groq: not configured" in output.err
    assert "fake-secret-key" not in output.out + output.err + store.path.read_text(encoding="utf-8")


def test_interactive_rejects_login_and_logout_during_run_but_allows_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class PausingProvider:
        async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
            await asyncio.Event().wait()
            yield ResponseCompleted(AssistantMessage((Text("Unused"),)))

    def forbidden_prompt(prompt: str, stream: object = None) -> str:
        raise AssertionError("Login must not prompt while a model run is active")

    monkeypatch.setattr(getpass, "getpass", forbidden_prompt)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "Hello\n/login groq\n/logout groq\n/auth-status groq\n/quit\n",
        ),
    )

    async def case() -> None:
        store = FileCredentialStore()
        await store.modify("groq", lambda current: ApiKeyCredential("existing-key"))
        agent = Agent(PausingProvider())
        assert await interactive(agent, GroqAuth(store)) == 0
        assert await store.read("groq") == ApiKeyCredential("existing-key")
        assert not agent.is_running

    asyncio.run(case())
    output = capsys.readouterr()
    assert output.err.count("Wait for the active run to finish") == 2
    assert "configured via stored credential" in output.err


def test_interactive_groq_opens_without_key_but_prompt_fails_locally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO("Hello\n"))
    args = argparse.Namespace(
        provider="groq",
        model="fake-model",
        demo=False,
        workspace=str(tmp_path),
        session=None,
        max_turns=2,
        trace=False,
        prompt=None,
    )
    assert asyncio.run(cli.run(args)) == 0
    output = capsys.readouterr()
    assert "Use /login groq" in output.err
    assert "auth login groq" in output.err
    assert "Real HTTP requests" not in output.err


def test_tests_disable_dotenv_and_block_real_http(tmp_path: Path) -> None:
    # This file contains test values only; no real configuration is read.
    path = tmp_path / ".env"
    path.write_text("GROQ_API_KEY=must-not-load", encoding="utf-8")
    assert not load_dotenv(path)
    assert "GROQ_API_KEY" not in os.environ
    with httpx2.Client() as client:
        with pytest.raises(AssertionError, match="Real HTTP requests"):
            client.get("https://example.invalid")


@pytest.mark.parametrize("override", [None, "explicit-model"])
def test_groq_cli_selects_its_key_endpoint_and_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: str | None,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    monkeypatch.setenv("GROQ_MODEL", "groq-model")
    monkeypatch.setenv("ANTHROPIC_MODEL", "wrong-provider-model")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-provider-key")
    captured: list[dict[str, JsonValue]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fake-groq-key"
        captured.append(TypeAdapter(dict[str, JsonValue]).validate_json(request.content))
        frame = {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
        }
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n".encode(),
        )

    def make_client(
        *,
        api_key: Callable[[], Awaitable[str]],
        base_url: str,
        timeout: float,
        max_retries: int,
    ) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )

    monkeypatch.setattr(cli, "AsyncOpenAI", make_client)
    args = argparse.Namespace(
        provider="groq",
        model=override,
        demo=False,
        workspace=str(tmp_path),
        session=str(tmp_path / "test.jsonl"),
        max_turns=2,
        trace=False,
        prompt="Hi",
    )
    assert asyncio.run(cli.run(args)) == 0
    assert captured[0]["model"] == (override or "groq-model")


def test_groq_missing_key_fails_before_session_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "a-key-for-another-provider")
    args = argparse.Namespace(
        provider="groq",
        model="groq-model",
        demo=False,
        workspace=str(tmp_path),
        session=None,
        max_turns=2,
        trace=False,
        prompt="Hi",
    )
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        asyncio.run(cli.run(args))
    assert not (tmp_path / ".pi-in-py").exists()


@pytest.mark.parametrize("value", ["0", "-1", "bad"])
def test_invalid_context_override_fails_before_session_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GROQ_CONTEXT_WINDOW", value)
    args = argparse.Namespace(
        provider="groq",
        model="qwen/qwen3.8-27b",
        demo=False,
        workspace=str(tmp_path),
        session=None,
        max_turns=2,
        trace=False,
        prompt="Hi",
    )
    with pytest.raises(ValueError, match="positive integer"):
        asyncio.run(cli.run(args))
    assert not (tmp_path / ".pi-in-py").exists()
