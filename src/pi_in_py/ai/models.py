"""A small offline capacity catalog; overrides handle other models and deployments."""

from collections.abc import Mapping

# Verified 2026-10-29: https://console.groq.com/docs/models
_GROQ = dict.fromkeys(
    (
        "qwen/qwen3.8-27b",
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    ),
    131_072,
)
# Verified 2026-10-29: https://platform.claude.com/docs/en/models/overview
# Dated IDs: /models/sonnet-4-5/overview and /models/haiku-4-5/overview.
_ANTHROPIC = {
    "claude-sonnet-5-5": 1_000_000,
    "claude-haiku-5-5": 1_000_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}
CONTEXT_WINDOWS = {"groq": _GROQ, "anthropic": _ANTHROPIC}


def resolve_context_window(
    provider: str, model: str, override: int | None, environ: Mapping[str, str]
) -> int | None:
    value: int | str | None = override
    if value is None:
        value = environ.get(f"{provider.upper()}_CONTEXT_WINDOW")
    if value is not None:
        try:
            window = int(value)
        except ValueError as error:
            raise ValueError("Context window must be a positive integer") from error
        if window < 1:
            raise ValueError("Context window must be a positive integer")
        return window
    return CONTEXT_WINDOWS.get(provider, {}).get(model)
