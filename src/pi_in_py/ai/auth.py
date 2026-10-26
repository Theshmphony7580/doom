from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol


class AuthError(ValueError):
    """Authentication configuration failed without exposing secret values."""


@dataclass(frozen=True)
class ApiKeyCredential:
    key: str = field(repr=False)
    type: Literal["api_key"] = "api_key"

    def __post_init__(self) -> None:
        if (
            not self.key
            or not self.key.isascii()
            or any(not char.isprintable() or char.isspace() for char in self.key)
        ):
            raise AuthError("API key must be nonempty printable ASCII without whitespace")


@dataclass(frozen=True)
class AuthResult:
    key: str = field(repr=False)
    source: Literal["explicit key", "stored credential", "GROQ_API_KEY"]


class CredentialStore(Protocol):
    async def read(self, provider_id: str) -> ApiKeyCredential | None: ...

    async def modify(
        self,
        provider_id: str,
        update: Callable[[ApiKeyCredential | None], ApiKeyCredential],
    ) -> ApiKeyCredential: ...

    async def delete(self, provider_id: str) -> None: ...
