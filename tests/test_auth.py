import asyncio
from pathlib import Path

import httpx2
import pytest
from openai import AsyncOpenAI

from pi_in_py.ai.auth import ApiKeyCredential, AuthError, AuthResult
from pi_in_py.ai.openai import OpenAIChatProvider
from pi_in_py.ai.providers.groq import BASE_URL, GroqAuth
from pi_in_py.ai.types import Request, UserMessage
from pi_in_py.app.auth_store import FileCredentialStore


def test_key_priority_and_logout_environment_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def case() -> None:
        store = FileCredentialStore(tmp_path / "auth.json")
        auth = GroqAuth(store)
        assert await auth.resolve() is None
        monkeypatch.setenv("GROQ_API_KEY", "environment-key")
        assert await auth.resolve() == AuthResult("environment-key", "GROQ_API_KEY")
        await store.modify("groq", lambda current: ApiKeyCredential("saved-key"))
        assert await auth.resolve() == AuthResult("saved-key", "stored credential")
        assert await GroqAuth(store, api_key="explicit-key").resolve() == AuthResult(
            "explicit-key",
            "explicit key",
        )
        await auth.logout()
        assert await auth.resolve() == AuthResult("environment-key", "GROQ_API_KEY")
        monkeypatch.delenv("GROQ_API_KEY")
        with pytest.raises(AuthError, match="auth login groq"):
            await auth.get_api_key()

    asyncio.run(case())


def test_explicit_key_bypasses_storage_but_corrupt_storage_does_not_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "auth.json"
    path.write_text("broken-secret-json", encoding="utf-8")
    store = FileCredentialStore(path)
    monkeypatch.setenv("GROQ_API_KEY", "environment-key")
    assert asyncio.run(GroqAuth(store, api_key="explicit-key").get_api_key()) == "explicit-key"
    with pytest.raises(AuthError, match="Invalid auth.json") as failure:
        asyncio.run(GroqAuth(store).resolve())
    assert "broken-secret-json" not in str(failure.value)


@pytest.mark.parametrize("key", ["", " ", "key with spaces", "key\nvalue", "key\x00", "secrét"])
def test_invalid_keys_fail_without_revealing_the_value(key: str) -> None:
    with pytest.raises(AuthError) as failure:
        ApiKeyCredential(key)
    if key.strip():
        assert key not in str(failure.value)


def test_credentials_are_hidden_in_representations() -> None:
    assert "fake-secret-key" not in repr(ApiKeyCredential("fake-secret-key"))
    assert "fake-secret-key" not in repr(AuthResult("fake-secret-key", "stored credential"))


def test_login_and_cancelled_login(tmp_path: Path) -> None:
    async def case() -> None:
        store = FileCredentialStore(tmp_path / "auth.json")
        auth = GroqAuth(store)

        async def cancelled(prompt: str) -> str | None:
            return None

        with pytest.raises(AuthError, match="cancelled"):
            await auth.login(cancelled)
        assert not store.path.exists()

        async def entered(prompt: str) -> str:
            assert prompt == "Enter Groq API key: "
            return "  fake-secret-key  "

        await auth.login(entered)
        assert await store.read("groq") == ApiKeyCredential("fake-secret-key")

    asyncio.run(case())


def test_sdk_resolves_changed_and_removed_credentials_before_each_request(tmp_path: Path) -> None:
    async def case() -> None:
        store = FileCredentialStore(tmp_path / "auth.json")
        auth = GroqAuth(store)
        headers: list[str] = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            assert str(request.url) == BASE_URL + "/chat/completions"
            headers.append(request.headers["authorization"])
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"id":"test","object":"chat.completion.chunk","created":1,'
                    b'"model":"test","choices":[{"index":0,"delta":{"content":"Hello"},'
                    b'"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                ),
            )

        async with AsyncOpenAI(
            api_key=auth.get_api_key,
            base_url=BASE_URL,
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            provider = OpenAIChatProvider(client, "test-model")
            await store.modify("groq", lambda current: ApiKeyCredential("first-key"))
            _ = [event async for event in provider.stream(Request((UserMessage("Hi"),), ()))]
            await store.modify("groq", lambda current: ApiKeyCredential("second-key"))
            _ = [event async for event in provider.stream(Request((UserMessage("Hi"),), ()))]
            await auth.logout()
            with pytest.raises(AuthError, match="GROQ_API_KEY"):
                _ = [event async for event in provider.stream(Request((UserMessage("Hi"),), ()))]
        assert headers == ["Bearer first-key", "Bearer second-key"]

    asyncio.run(case())
