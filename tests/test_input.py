import asyncio
import getpass
import sys
import warnings
from io import StringIO
from threading import get_ident

import pytest

from pi_in_py.ai.auth import AuthError
from pi_in_py.app.input import InputReader


def test_one_reader_handles_commands_and_hidden_prompts_without_reading_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[int] = []

    class Input:
        def __init__(self, text: str) -> None:
            self.stream = StringIO(text)

        def readline(self) -> str:
            reads.append(get_ident())
            return self.stream.readline()

    monkeypatch.setattr(sys, "stdin", Input("/login groq\n/quit\n"))

    def secret(prompt: str, stream: object = None) -> str:
        assert prompt == "Key: "
        assert len(reads) == 1 and reads[0] == get_ident()
        return "fake-secret"

    monkeypatch.setattr(getpass, "getpass", secret)

    async def case() -> None:
        reader = InputReader()
        try:
            assert await reader.read() == "/login groq\n"
            assert await reader.secret("Key: ") == "fake-secret"
            assert len(reads) == 1
            assert await reader.read() == "/quit\n"
            assert len(set(reads)) == 1
        finally:
            reader.close()

    asyncio.run(case())


def test_hidden_prompt_rejects_an_echoing_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def unsafe_prompt(prompt: str, stream: object = None) -> str:
        warnings.warn("Cannot disable echo", getpass.GetPassWarning, stacklevel=1)
        raise AssertionError("Input must not be read after echo suppression fails")

    monkeypatch.setattr(getpass, "getpass", unsafe_prompt)

    async def case() -> None:
        reader = InputReader()
        try:
            with pytest.raises(AuthError, match="echo disabled"):
                await reader.secret("Key: ")
        finally:
            reader.close()

    asyncio.run(case())
