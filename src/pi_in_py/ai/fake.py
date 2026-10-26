import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Sequence

from pi_in_py.ai.types import (
    AssistantMessage,
    ProviderError,
    ProviderEvent,
    Request,
    ResponseCompleted,
    Text,
    TextDelta,
)


class ScriptedProvider:
    """A deterministic provider for tests and the offline CLI demo."""

    def __init__(self, responses: Sequence[AssistantMessage | Exception]) -> None:
        self.responses = deque(responses)
        self.requests: list[Request] = []

    async def stream(self, request: Request) -> AsyncGenerator[ProviderEvent]:
        self.requests.append(request)
        if not self.responses:
            raise ProviderError("The scripted provider has no responses left")
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        for block in response.content:
            if isinstance(block, Text):
                for offset in range(0, len(block.text), 12):
                    await asyncio.sleep(0)
                    yield TextDelta(block.text[offset : offset + 12])
        yield ResponseCompleted(response)
