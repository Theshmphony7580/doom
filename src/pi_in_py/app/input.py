import asyncio
import getpass
import sys
import warnings
from queue import Queue
from threading import Thread

from pi_in_py.ai.auth import AuthError


class InputReader:
    """One daemon thread owns stdin, including hidden credential prompts."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._requests: Queue[tuple[asyncio.Future[str | None], str | None] | None] = Queue()
        Thread(target=self._run, daemon=True).start()

    async def read(self) -> str | None:
        future: asyncio.Future[str | None] = self._loop.create_future()
        self._requests.put((future, None))
        return await future

    async def secret(self, prompt: str) -> str | None:
        future: asyncio.Future[str | None] = self._loop.create_future()
        self._requests.put((future, prompt))
        return await future

    def close(self) -> None:
        self._requests.put(None)

    @staticmethod
    def _finish(
        future: asyncio.Future[str | None],
        value: str | None,
        error: Exception | None,
    ) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value)

    def _run(self) -> None:
        while (request := self._requests.get()) is not None:
            future, prompt = request
            value: str | None = None
            error: Exception | None = None
            try:
                if prompt is None:
                    value = sys.stdin.readline() or None
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", getpass.GetPassWarning)
                        value = getpass.getpass(prompt, stream=sys.stderr)
            except (EOFError, KeyboardInterrupt):
                pass
            except getpass.GetPassWarning:
                error = AuthError("Hidden API-key input requires a terminal with echo disabled")
            except Exception as failure:
                error = failure
            try:
                self._loop.call_soon_threadsafe(self._finish, future, value, error)
            except RuntimeError:
                return
