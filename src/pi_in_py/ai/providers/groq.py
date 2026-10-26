import os
from collections.abc import Awaitable, Callable

from pi_in_py.ai.auth import ApiKeyCredential, AuthError, AuthResult, CredentialStore

BASE_URL = "https://api.groq.com/openai/v1"


class GroqAuth:
    def __init__(self, credentials: CredentialStore, *, api_key: str | None = None) -> None:
        self.credentials = credentials
        self._api_key = api_key

    async def resolve(self) -> AuthResult | None:
        if self._api_key is not None:
            return AuthResult(ApiKeyCredential(self._api_key).key, "explicit key")
        stored = await self.credentials.read("groq")
        if stored is not None:
            return AuthResult(stored.key, "stored credential")
        key = os.environ.get("GROQ_API_KEY", "").strip()
        return AuthResult(ApiKeyCredential(key).key, "GROQ_API_KEY") if key else None

    async def get_api_key(self) -> str:
        result = await self.resolve()
        if result is None:
            raise AuthError("Run 'pi-in-py auth login groq' or set GROQ_API_KEY")
        return result.key

    async def login(self, prompt: Callable[[str], Awaitable[str | None]]) -> None:
        key = await prompt("Enter Groq API key: ")
        if key is None:
            raise AuthError("Login cancelled")
        credential = ApiKeyCredential(key.strip())
        await self.credentials.modify("groq", lambda current: credential)

    async def logout(self) -> None:
        await self.credentials.delete("groq")
