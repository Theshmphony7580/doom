import asyncio
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from filelock import FileLock, Timeout
from pydantic import JsonValue, TypeAdapter

from pi_in_py.ai.auth import ApiKeyCredential, AuthError

_DATA = TypeAdapter(dict[str, JsonValue])
_CREDENTIAL = TypeAdapter(ApiKeyCredential)


class FileCredentialStore:
    def __init__(self, path: Path | None = None, *, lock_timeout: float = 5) -> None:
        self.path = path if path is not None else Path.home() / ".pi-in-py" / "auth.json"
        self.lock_timeout = lock_timeout

    @contextmanager
    def _locked_data(self) -> Iterator[dict[str, JsonValue]]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            # Lock a stable sidecar: auth.json itself is replaced after each update.
            with FileLock(str(self.path) + ".lock", timeout=self.lock_timeout, mode=0o600):
                try:
                    data = _DATA.validate_json(self.path.read_text(encoding="utf-8"), strict=True)
                except FileNotFoundError:
                    data = {}
                except ValueError:
                    raise AuthError(
                        "Invalid auth.json; repair the file before changing credentials"
                    ) from None
                yield data
        except Timeout:
            raise AuthError(
                "Credential store is busy; try again after the other update finishes"
            ) from None

    @staticmethod
    def _credential(value: JsonValue) -> ApiKeyCredential:
        if not isinstance(value, dict) or value.get("type") != "api_key":
            raise AuthError("Invalid stored API-key credential; repair auth.json")
        try:
            return _CREDENTIAL.validate_json(json.dumps(value), strict=True)
        except ValueError:
            raise AuthError("Invalid stored API-key credential; repair auth.json") from None

    def _write(self, data: dict[str, JsonValue]) -> None:
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".auth-",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary = Path(file.name)
                json.dump(data, file, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def read(self, provider_id: str) -> ApiKeyCredential | None:
        return await asyncio.to_thread(self._read, provider_id)

    def _read(self, provider_id: str) -> ApiKeyCredential | None:
        if not self.path.exists():
            return None
        with self._locked_data() as data:
            return self._credential(data[provider_id]) if provider_id in data else None

    async def modify(
        self,
        provider_id: str,
        update: Callable[[ApiKeyCredential | None], ApiKeyCredential],
    ) -> ApiKeyCredential:
        return await asyncio.to_thread(self._modify, provider_id, update)

    def _modify(
        self,
        provider_id: str,
        update: Callable[[ApiKeyCredential | None], ApiKeyCredential],
    ) -> ApiKeyCredential:
        with self._locked_data() as data:
            current = self._credential(data[provider_id]) if provider_id in data else None
            credential = update(current)
            data[provider_id] = {"type": credential.type, "key": credential.key}
            self._write(data)
            return credential

    async def delete(self, provider_id: str) -> None:
        await asyncio.to_thread(self._delete, provider_id)

    def _delete(self, provider_id: str) -> None:
        if not self.path.exists():
            return
        with self._locked_data() as data:
            if provider_id in data:
                self._credential(data[provider_id])
                del data[provider_id]
                self._write(data)
