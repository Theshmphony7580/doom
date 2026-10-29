import asyncio
import json
import os
import queue
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from threading import Thread
from typing import cast

import pytest

from pi_in_py.agent.agent import Agent
from pi_in_py.ai.providers.groq import GroqAuth
from pi_in_py.ai.types import ProviderEvent, Request, TextDelta
from pi_in_py.app.auth_store import FileCredentialStore
from pi_in_py.app.session import SessionStore
from pi_in_py.app.ui_protocol import ProtocolSession


class ProtocolProcess:
    def __init__(self, workspace: Path, session: Path, home: Path, *args: str) -> None:
        env = os.environ.copy()
        env.update({"HOME": str(home), "USERPROFILE": str(home), "PYTHON_DOTENV_DISABLED": "1"})
        env.pop("GROQ_API_KEY", None)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "pi_in_py",
                "--ui-protocol",
                "--demo",
                "--workspace",
                str(workspace),
                "--session",
                str(session),
                *args,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            env=env,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.errors: list[str] = []
        assert self.process.stdout is not None

        def read_lines() -> None:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.lines.put(line)

        Thread(target=read_lines, daemon=True).start()
        assert self.process.stderr is not None
        self.error_reader = Thread(
            target=lambda: self.errors.extend(self.process.stderr or ()), daemon=True
        )
        self.error_reader.start()

    def send(self, packet: dict[str, object]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(packet) + "\n")
        self.process.stdin.flush()

    def until(self, packet_type: str, event_type: str | None = None) -> list[dict[str, object]]:
        seen: list[dict[str, object]] = []
        while True:
            packet = cast(dict[str, object], json.loads(self.lines.get(timeout=15)))
            seen.append(packet)
            event = packet.get("event")
            if packet.get("type") == packet_type and (
                event_type is None or isinstance(event, dict) and event.get("type") == event_type
            ):
                return seen

    def close(self) -> str:
        self.send({"type": "quit"})
        self.process.wait(timeout=15)
        self.error_reader.join(timeout=5)
        result = "".join(self.errors)
        assert self.process.returncode == 0, result
        return result


def test_protocol_demo_resume_and_hidden_login(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("demo input", encoding="utf-8")
    session = tmp_path / "session.jsonl"
    child = ProtocolProcess(tmp_path, session, tmp_path, "--trace")
    try:
        ready = child.until("ready")[-1]
        assert ready["messages"] == []
        usage = ready["context_usage"]
        assert isinstance(usage, dict) and usage["context_window"] is None
        events = child.until("event", "run_end")
        event_types = [
            event.get("type") for item in events if isinstance(event := item.get("event"), dict)
        ]
        assert "text_delta" in event_types
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert "context_update" in event_types
        child.send({"type": "submit", "text": "/login groq"})
        child.until("secret_request")
        key = "fake-private-key"
        child.send({"type": "secret", "value": key})
        notices = child.until("notice")
        assert key not in json.dumps(notices)
        stderr = child.close()
        assert key not in stderr
        assert key not in session.read_text(encoding="utf-8")
    finally:
        if child.process.poll() is None:
            child.process.kill()
            child.process.wait()

    resumed = ProtocolProcess(tmp_path, session, tmp_path)
    try:
        ready = resumed.until("ready")[-1]
        messages = cast(list[dict[str, object]], ready["messages"])
        assert [message["role"] for message in messages] == [
            "system",
            "user",
            "assistant",
            "tool_result",
            "tool_result",
            "assistant",
        ]
        resumed.until("event", "run_end")
        resumed.close()
    finally:
        if resumed.process.poll() is None:
            resumed.process.kill()
            resumed.process.wait()


def test_cancellation_preserves_queued_input_and_closes_pending_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        started = asyncio.Event()

        class WaitingProvider:
            async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
                started.set()
                await asyncio.Event().wait()
                yield TextDelta("unused")

        packets: list[dict[str, object]] = []
        monkeypatch.setattr("pi_in_py.app.ui_protocol.send", packets.append)
        agent = Agent(WaitingProvider())
        session = ProtocolSession(
            agent,
            GroqAuth(FileCredentialStore(tmp_path / "auth.json")),
            SessionStore(tmp_path / "session.jsonl", tmp_path),
            False,
        )
        agent.subscribe(session.on_event)
        await session.submit("first")
        await asyncio.wait_for(started.wait(), timeout=5)
        await session.submit("/steer later")
        await session.submit("/followup final")
        await session.submit("/login groq")
        assert packets[-1]["type"] == "error"
        await session.submit("/cancel")
        assert session.active is not None
        result = await asyncio.wait_for(session.active, timeout=5)
        assert result.reason == "cancelled"
        assert agent.queued_messages == (1, 1)
        await session.submit("/login groq")
        await asyncio.wait_for(session.close(True), timeout=5)

    asyncio.run(run())
