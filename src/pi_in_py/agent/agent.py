import asyncio
from collections import deque
from collections.abc import Callable, Sequence

from pi_in_py.agent.compaction import (
    Compaction,
    compact_context,
    project_context,
    validate_compaction,
)
from pi_in_py.agent.loop import ToolBudget, run_turn
from pi_in_py.agent.types import AgentEvent, EventSink, RunResult, StopReason, Tool
from pi_in_py.ai.context import ContextUsage, estimate_context
from pi_in_py.ai.types import Message, Provider, SystemMessage, ToolSpec, UserMessage

TOOL_LIMIT_NOTICE = (
    "Tool-call budget exhausted for this run. Tools are unavailable. "
    "Finish using available evidence and report unresolved gaps; do not request more tools."
)


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: Sequence[Tool] = (),
        *,
        system_prompt: str | None = None,
        messages: Sequence[Message] = (),
        max_turns: int = 5,
        max_tool_calls: int = 20,
        tool_timeout: float = 30,
        context_window: int | None = None,
        auto_compact: bool = True,
        compaction: Compaction | None = None,
        save_compaction: Callable[[Compaction], None] | None = None,
    ) -> None:
        if max_turns < 1 or tool_timeout <= 0:
            raise ValueError("max_turns and tool_timeout must be positive")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be positive")
        if context_window is not None and context_window < 1:
            raise ValueError("context_window must be positive")
        self.context_window = context_window
        self.auto_compact = auto_compact and context_window is not None
        self.compaction = compaction
        self._save_compaction = save_compaction
        self._usage_floor = len(messages)
        self._awaiting_usage = compaction is not None
        self.provider = provider
        self.tools = {tool.spec.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Tool names must be unique")
        self.max_turns = max_turns
        self._tool_budget = ToolBudget(max_tool_calls)
        self.tool_timeout = tool_timeout
        self._messages: list[Message] = (
            list(messages)
            if messages
            else [
                SystemMessage(
                    system_prompt if system_prompt is not None else "You are a helpful assistant."
                )
            ]
        )
        if not isinstance(self._messages[0], SystemMessage):
            raise ValueError("Messages must start with a system message")
        self._system_prompt = (
            system_prompt if system_prompt is not None else self._messages[0].content
        )
        if compaction is not None:
            validate_compaction(compaction, self._messages)
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
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def context_messages(self) -> tuple[Message, ...]:
        return project_context(self._request_messages, self.compaction)

    @property
    def _request_messages(self) -> tuple[Message, ...]:
        prompt = self._system_prompt
        if self.tool_calls_used >= self.max_tool_calls:
            prompt += "\n\n" + TOOL_LIMIT_NOTICE
        return (SystemMessage(prompt), *self._messages[1:])

    @property
    def max_tool_calls(self) -> int:
        return self._tool_budget.limit

    @property
    def tool_calls_used(self) -> int:
        return self._tool_budget.used

    @property
    def _available_specs(self) -> tuple[ToolSpec, ...]:
        if self.tool_calls_used >= self.max_tool_calls:
            return ()
        return tuple(tool.spec for tool in self.tools.values())

    def get_context_usage(self) -> ContextUsage:
        messages = self.context_messages
        floor = self._usage_floor
        if self.compaction is not None:
            floor = 2 + max(0, floor - self.compaction.first_kept_index)
        tokens, estimated = estimate_context(messages, self._available_specs, usage_start=floor)
        known = not self._awaiting_usage
        window = self.context_window
        return ContextUsage(
            tokens if known else None,
            tokens,
            window,
            tokens / window * 100 if known and window else None,
            estimated,
            self.auto_compact,
        )

    async def _emit_context(self) -> None:
        await self._emit(AgentEvent("context_update", context_usage=self.get_context_usage()))

    async def _prepare_context(self, checkpoint: int) -> int | None:
        window = self.context_window
        if window is None:
            return None
        threshold = window - min(16384, window // 4)
        if self.auto_compact and self.get_context_usage().estimated_tokens > threshold:
            await self._emit(AgentEvent("compaction_start"))
            try:
                result = await compact_context(
                    self.provider,
                    self._request_messages,
                    self._available_specs,
                    checkpoint,
                    window,
                    self.compaction,
                )
                # No await between durable storage and activation: cancellation cannot split them.
                if self._save_compaction is not None:
                    self._save_compaction(result)
                self.compaction = result
                self._usage_floor = checkpoint
                self._awaiting_usage = True
            except asyncio.CancelledError:
                await self._emit(AgentEvent("compaction_end", error="Compaction cancelled"))
                raise
            except Exception as error:
                await self._emit(AgentEvent("compaction_end", error=str(error)))
                raise
            await self._emit(AgentEvent("compaction_end"))
            await self._emit_context()
        available = (
            window - self.get_context_usage().estimated_tokens - min(4096, max(1, window // 16))
        )
        if available < 1:
            raise ValueError("Context window is full; reduce input or use a larger model")
        return min(4096, available)

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
        # A previous response's usage included the prior run's budget notice/tool definitions.
        if self.tool_calls_used >= self.max_tool_calls:
            self._usage_floor = len(self._messages)
        self._tool_budget.used = 0
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
                await self._emit_context()
                output_limit = await self._prepare_context(checkpoint)
                await self._emit(AgentEvent("turn_start"))
                exhausted = self.tool_calls_used >= self.max_tool_calls
                response, results = await run_turn(
                    self.provider,
                    self.context_messages,
                    self.tools,
                    self._emit,
                    self.tool_timeout,
                    output_limit,
                    budget=self._tool_budget,
                )
                self._messages.extend((response, *results))
                if response.usage.total_tokens > 0:
                    self._awaiting_usage = False
                # A whole turn is committed before session/UI subscribers see turn_end.
                checkpoint = len(self._messages)
                if not exhausted and self.tool_calls_used >= self.max_tool_calls:
                    self._usage_floor = checkpoint
                selected = None
                await self._emit(AgentEvent("turn_end", messages=self.messages))
                await self._emit_context()
                if response.stop_reason == "length":
                    reason, error = "length", "Model output or context limit reached"
                    break
                if exhausted and results:
                    reason, error = (
                        "tool_limit",
                        "Model requested tools after the tool-call budget was exhausted",
                    )
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
                await self._emit_context()
                await self._emit(
                    AgentEvent(
                        "run_end",
                        messages=self.messages,
                        reason=reason,
                        error=error,
                        context_usage=self.get_context_usage(),
                    )
                )
            finally:
                self._task = None
                self._started = False
        return RunResult(reason, tuple(self._messages[run_start:]), error)
