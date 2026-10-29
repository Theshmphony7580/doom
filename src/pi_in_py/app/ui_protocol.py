"""Private JSON-lines transport between the terminal frontend and the agent."""

import asyncio
import json
import sys
from dataclasses import asdict
from typing import cast

from pydantic import JsonValue

from pi_in_py.agent.agent import Agent
from pi_in_py.agent.types import AgentEvent, RunResult
from pi_in_py.ai.context import format_context
from pi_in_py.ai.providers.groq import GroqAuth
from pi_in_py.app.input import InputReader
from pi_in_py.app.prompt import prompt_hash
from pi_in_py.app.session import SessionStore


def send(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True), flush=True)


class ProtocolSession:
    def __init__(self, agent: Agent, auth: GroqAuth, store: SessionStore, trace: bool) -> None:
        self.agent = agent
        self.auth = auth
        self.store = store
        self.trace = trace
        self.active: asyncio.Task[RunResult] | None = None
        self.login: asyncio.Task[None] | None = None
        self.secret: asyncio.Future[str | None] | None = None

    async def on_event(self, event: AgentEvent) -> None:
        if event.type == "turn_end":
            self.store.append_turn(event.messages)
        payload = asdict(event)
        send({"type": "event", "event": payload})
        if self.trace and event.type != "text_delta":
            print(json.dumps(payload, ensure_ascii=True), file=sys.stderr, flush=True)

    async def login_groq(self) -> None:
        async def prompt(_: str) -> str | None:
            self.secret = asyncio.get_running_loop().create_future()
            send({"type": "secret_request", "prompt": "Enter Groq API key: "})
            try:
                return await self.secret
            finally:
                self.secret = None

        try:
            await self.auth.login(prompt)
            send(
                {"type": "notice", "text": "Groq API key saved; the next request will validate it."}
            )
            await self.auth_status()
        except (ValueError, OSError) as error:
            send({"type": "error", "text": str(error)})

    async def auth_status(self) -> None:
        result = await self.auth.resolve()
        text = f"configured via {result.source} (not validated)" if result else "not configured"
        send({"type": "notice", "text": f"Groq: {text}"})

    async def submit(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        command, _, argument = text.partition(" ")
        try:
            if command == "/quit":
                return True
            if command == "/help":
                send({"type": "help"})
            elif command == "/status":
                steering, followups = self.agent.queued_messages
                send(
                    {
                        "type": "notice",
                        "text": (
                            f"running={self.agent.is_running} "
                            f"steering={steering} followups={followups}"
                            f" tools={self.agent.tool_calls_used}/{self.agent.max_tool_calls}"
                            f" prompt={prompt_hash(self.agent.system_prompt)}"
                            f" {format_context(self.agent.get_context_usage())}"
                        ),
                    }
                )
            elif command == "/cancel":
                self.agent.cancel()
            elif command in ("/login", "/logout", "/auth-status"):
                if argument.strip() != "groq":
                    raise ValueError("Specify groq; only Groq credential commands are supported")
                if command != "/auth-status" and self.agent.is_running:
                    raise ValueError("Wait for the active run to finish before login or logout")
                if self.login and not self.login.done():
                    raise ValueError("A login is already in progress")
                if command == "/login":
                    self.login = asyncio.create_task(self.login_groq())
                elif command == "/logout":
                    await self.auth.logout()
                    send(
                        {
                            "type": "notice",
                            "text": "Saved Groq credential removed; the key was not revoked.",
                        }
                    )
                    await self.auth_status()
                else:
                    await self.auth_status()
            elif command in ("/steer", "/followup"):
                if not self.agent.is_running:
                    self.active = self.agent.start(argument)
                elif command == "/steer":
                    self.agent.steer(argument)
                else:
                    self.agent.follow_up(argument)
            elif command.startswith("/"):
                send({"type": "error", "text": f"Unknown command: {command}"})
            elif self.agent.is_running:
                self.agent.steer(text)
            else:
                self.active = self.agent.start(text)
        except (ValueError, RuntimeError, OSError) as error:
            send({"type": "error", "text": str(error)})
        return False

    async def close(self, cancel: bool) -> None:
        if self.secret and not self.secret.done():
            self.secret.set_result(None)
        elif self.login and not self.login.done():
            self.login.cancel()
        if self.login and not self.login.cancelled():
            try:
                await self.login
            except asyncio.CancelledError:
                pass
        if self.active:
            if cancel and not self.active.done():
                self.agent.cancel()
            await self.active


async def run_ui_protocol(
    agent: Agent,
    auth: GroqAuth,
    store: SessionStore,
    provider: str,
    model: str | None,
    trace: bool,
    prompt: str | None,
    demo: bool,
    *,
    prompt_updated: bool = False,
) -> int:
    session = ProtocolSession(agent, auth, store, trace)
    agent.subscribe(session.on_event)
    send(
        {
            "type": "ready",
            "workspace": str(store.workspace),
            "session": str(store.path),
            "provider": provider,
            "model": model or "demo",
            "messages": [asdict(message) for message in store.messages],
            "context_usage": asdict(agent.get_context_usage()),
        }
    )
    if prompt_updated:
        send({"type": "notice", "text": "Session now uses the current Doom instructions"})
    if agent.context_window is None and not demo:
        send(
            {
                "type": "notice",
                "text": "Unknown model capacity; auto compaction is off. Set --context-window "
                f"or {provider.upper()}_CONTEXT_WINDOW.",
            }
        )
    if prompt is not None or demo:
        session.active = agent.start(prompt or "Demonstrate the tool loop.")
    reader = InputReader()
    cancel = False
    line: str | None = None
    try:
        while (line := await reader.read()) is not None:
            try:
                value = cast(JsonValue, json.loads(line))
                if not isinstance(value, dict):
                    raise ValueError("Expected a JSON object")
                kind = value.get("type")
                text = value.get("text")
                secret_value = value.get("value")
                if kind == "submit" and isinstance(text, str):
                    if await session.submit(text):
                        cancel = True
                        break
                elif kind == "secret" and (secret_value is None or isinstance(secret_value, str)):
                    if session.secret and not session.secret.done():
                        session.secret.set_result(secret_value)
                elif kind == "quit":
                    cancel = True
                    break
                else:
                    raise ValueError("Invalid protocol message")
            except ValueError as error:
                send({"type": "error", "text": str(error)})
    finally:
        reader.close()
        await session.close(cancel or line is None)
    return 0
