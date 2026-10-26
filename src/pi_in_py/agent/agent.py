import asyncio
from collections import deque
from collections.abc import Sequence

from pi_in_py.agent.loop import run_turn
from pi_in_py.agent.types import AgentEvent, EventSink, RunResult, StopReason, Tool
from pi_in_py.ai.types import Message, Provider, SystemMessage, UserMessage


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: Sequence[Tool] = (),
        *,
        system_prompt: str = "You are a helpful assistant.",
        messages: Sequence[Message] = (),
        max_turns: int = 5,
        tool_timeout: float = 30,
    ) -> None:
        if max_turns < 1 or tool_timeout <= 0:
            raise ValueError("max_turns and tool_timeout must be positive")
        self.provider = provider
        self.tools = {tool.spec.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Tool names must be unique")
        self.max_turns = max_turns
        self.tool_timeout = tool_timeout
        self._messages: list[Message] = (
            list(messages) if messages else [SystemMessage(system_prompt)]
        )
        self._steering: deque[UserMessage] = deque()
        self._followups: deque[UserMessage] = deque()
        self._listeners: list[EventSink] = []
        self._task: asyncio.Task[RunResult] | None = None
        self._started = False
        self._cancel_requested = False

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    @property
    def is_running(self) -> bool:
        return self._task is not None

    @property
    def queued_messages(self) -> tuple[int, int]:
        return len(self._steering), len(self._followups)

    def subscribe(self, listener: EventSink) -> None:
        self._listeners.append(listener)

    def steer(self, text: str) -> None:
        self._steering.append(self._user_message(text))

    def follow_up(self, text: str) -> None:
        self._followups.append(self._user_message(text))

    def start(self, text: str) -> asyncio.Task[RunResult]:
        if self.is_running:
            raise RuntimeError("Agent is already running; use steer() or follow_up()")
        message = self._user_message(text)
        self._started = False
        self._cancel_requested = False
        self._task = asyncio.create_task(self._run(message))
        return self._task

    async def prompt(self, text: str) -> RunResult:
        return await self.start(text)

    def cancel(self) -> None:
        if self._task is not None:
            self._cancel_requested = True
            if self._started:
                self._task.cancel()

    @staticmethod
    def _user_message(text: str) -> UserMessage:
        if not text.strip():
            raise ValueError("Input cannot be empty")
        return UserMessage(text)

    async def _emit(self, event: AgentEvent) -> None:
        for listener in self._listeners:
            await listener(event)

    async def _run(self, prompt: UserMessage) -> RunResult:
        self._started = True
        run_start = len(self._messages)
        checkpoint = run_start
        selected: tuple[deque[UserMessage], UserMessage] | None = None
        reason: StopReason = "complete"
        error: str | None = None
        try:
            await self._emit(AgentEvent("run_start"))
            if self._cancel_requested:
                raise asyncio.CancelledError
            self._messages.append(prompt)
            await self._emit(AgentEvent("message_end", message=prompt))
            if self._steering:
                selected = (self._steering, self._steering.popleft())
                self._messages.append(selected[1])
                await self._emit(AgentEvent("message_end", message=selected[1]))

            for turn in range(self.max_turns):
                await self._emit(AgentEvent("turn_start"))
                response, results = await run_turn(
                    self.provider, self.messages, self.tools, self._emit, self.tool_timeout
                )
                self._messages.extend((response, *results))
                # A whole turn is committed before session/UI subscribers see turn_end.
                checkpoint = len(self._messages)
                selected = None
                await self._emit(AgentEvent("turn_end", messages=self.messages))
                if response.stop_reason == "length":
                    reason, error = "length", "Model output or context limit reached"
                    break
                if turn + 1 == self.max_turns:
                    if results or self._steering or self._followups:
                        reason, error = "turn_limit", "Maximum turns reached"
                    break

                # Steering is selected only after the entire current tool batch.
                if self._steering:
                    selected = (self._steering, self._steering.popleft())
                elif results:
                    continue
                elif self._followups:
                    selected = (self._followups, self._followups.popleft())
                else:
                    break
                self._messages.append(selected[1])
                await self._emit(AgentEvent("message_end", message=selected[1]))
        except asyncio.CancelledError:
            reason = "cancelled"
        except Exception as failure:
            reason, error = "error", str(failure)
        finally:
            # shortcut: discard unfinished turns; durable in-flight tool recovery is outside v1.
            del self._messages[checkpoint:]
            if selected is not None:
                selected[0].appendleft(selected[1])
            try:
                await self._emit(
                    AgentEvent("run_end", messages=self.messages, reason=reason, error=error)
                )
            finally:
                self._task = None
                self._started = False
        return RunResult(reason, tuple(self._messages[run_start:]), error)
