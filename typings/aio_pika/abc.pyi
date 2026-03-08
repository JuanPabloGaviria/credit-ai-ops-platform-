from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

class AbstractProcessContext(Protocol):
    async def __aenter__(self) -> object: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None: ...

class AbstractIncomingMessage(Protocol):
    body: bytes

    def process(self, *, requeue: bool = ...) -> AbstractProcessContext: ...

class AbstractExchange(Protocol):
    async def publish(self, message: object, routing_key: str) -> None: ...

class AbstractQueue(Protocol):
    async def bind(self, exchange: AbstractExchange, routing_key: str) -> None: ...
    async def consume(
        self,
        callback: Callable[[AbstractIncomingMessage], Awaitable[None]],
    ) -> str: ...
    async def cancel(self, consumer_tag: str) -> None: ...
    async def get(self, *, fail: bool = ...) -> AbstractIncomingMessage | None: ...

class AbstractChannel(Protocol):
    async def set_qos(self, *, prefetch_count: int) -> None: ...
    async def declare_exchange(
        self,
        name: str,
        type_: object,
        *,
        durable: bool = ...,
    ) -> AbstractExchange: ...
    async def declare_queue(
        self,
        name: str,
        *,
        durable: bool = ...,
        arguments: dict[str, object] | None = ...,
    ) -> AbstractQueue: ...
    async def close(self) -> None: ...

class AbstractConnection(Protocol):
    async def channel(self, *, publisher_confirms: bool = ...) -> AbstractChannel: ...
    async def close(self) -> None: ...
