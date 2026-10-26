# Review Fixes for `src/pi_in_py/agent/types.py`

## 1. `Tool.execute` return type is too narrow

**Problem:** `execute: Callable[[dict[str, JsonValue]], Awaitable[str]]` restricts all tools to returning a plain string, even if `ToolResult` supports richer payloads.

**Fix:**

```python
# Before
if type Tool.execute -> Callable[[dict[str, JsonValue]], Awaitable[str]]

# After — allow JSON payloads so they can be coerced into a ToolResult
type ToolExecute = Callable[[dict[str, JsonValue]], Awaitable[str | dict[str, JsonValue]]]

@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    execute: ToolExecute
```

And update `define_tool`'s `execute` parameter signature to match.

## 2. `validated_execute` loses the tool's name in tracebacks

**Problem:** Every tool's internal coroutine shows up as `validated_execute` in stack traces, making debugging difficult.

**Fix:**

```python
def define_tool[P: BaseModel](
    name: str,
    description: str,
    parameters: type[P],
    execute: Callable[[P], Awaitable[str]],
) -> Tool:
    async def validated_execute(arguments: dict[str, JsonValue]) -> str:
        return await execute(parameters.model_validate(arguments, strict=True))

    validated_execute.__name__ = name
    validated_execute.__qualname__ = f"define_tool<{name}>.validated_execute"
    return Tool(ToolSpec(name, description, parameters.model_json_schema()), validated_execute)
```

## 3. `AgentEvent.delta: str = ""` conflates "absent" with "empty"

**Problem:** An empty-string default is ambiguous. A consumer cannot tell whether a `text_delta` event had no delta or an intentional empty one.

**Fix:**

```python
delta: str | None = None
```

Consumers should treat `None` as "no delta" and only render `""` when it actually arrives.

## 4. `AgentEvent` becomes unwieldy as it grows

**Problem:** A single dataclass with many optional fields forces every consumer to field-check after a `.type ==` comparison.

**Fix (optional, later):** Split into a discriminated hierarchy:

```python
@dataclass(frozen=True)
class AgentEvent:
    type: str

@dataclass(frozen=True)
class RunStartEvent(AgentEvent):
    type: Literal["run_start"] = "run_start"  # etc. per event kind
```

Not urgent at the current size; revisit when adding more than ~3–4 new event kinds.

## 5. Tool-level error channel is unclear

**Problem:** `AgentEvent` has both `result: ToolResult | None` and `error: str | None`. It's ambiguous whether a tool's failure sets `error` on the event or is encoded in `ToolResult`.

**Fix:** Add a module-level docstring comment:

```python
# AgentEvent.error is reserved for run-level failures (LLM errors, turn-limit, etc.).
# Tool-level failures are always encoded in the ToolResult carried by AgentEvent.result.
```

Or, better, add `is_error: bool = False` to `ToolResult` (upstream module) and use that exclusively.

## 6. Missing `__all__`

**Problem:** No explicit public API; `from pi_in_py.agent.types import *` would export private helpers if any are added.

**Fix:** Add at end of module:

```python
__all__ = [
    "StopReason",
    "Tool",
    "define_tool",
    "AgentEvent",
    "EventSink",
    "RunResult",
]
```

## 7. Confirm Python ≥ 3.12 for PEP 695/696

**Note:** `type StopReason = ...` (PEP 695) and `[P: BaseModel]` (PEP 696) require Python 3.12+. Confirm `.python-version` pins ≥ 3.12; otherwise these need to be rewritten with `typing.TypeAlias` and `TypeVar`.