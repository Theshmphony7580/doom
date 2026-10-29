"""Load the packaged identity once when composing an application session."""

from hashlib import sha256
from importlib.resources import files


def prompt_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def load_system_prompt() -> str:
    path = files(__package__).joinpath("prompts", "system.md")
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(f"Cannot read packaged system prompt: {error}") from error
    if not content:
        raise ValueError("Packaged system prompt is empty")
    return content
