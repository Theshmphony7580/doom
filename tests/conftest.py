from pathlib import Path

import httpx2
import pytest


@pytest.fixture(autouse=True)
def isolate_credentials_and_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in (
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_ADMIN_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    def reject_request(self: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("Real HTTP requests are disabled in tests; use MockTransport")

    async def reject_async_request(
        self: httpx2.AsyncHTTPTransport,
        request: httpx2.Request,
    ) -> httpx2.Response:
        raise AssertionError("Real HTTP requests are disabled in tests; use MockTransport")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", reject_request)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", reject_async_request)
