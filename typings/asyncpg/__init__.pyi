from __future__ import annotations

from typing import Any

class Record(dict[str, Any]): ...

class Connection:
    async def execute(self, query: str, *args: Any) -> str: ...
    async def fetch(self, query: str, *args: Any) -> list[Record]: ...
    async def fetchrow(self, query: str, *args: Any) -> Record | None: ...
    async def close(self) -> None: ...

class PoolAcquireContext:
    async def __aenter__(self) -> Connection: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None: ...

class Pool:
    def acquire(self, *, timeout: float | None = None) -> PoolAcquireContext: ...
    async def close(self) -> None: ...

async def create_pool(
    dsn: str | None = None,
    *,
    min_size: int = ...,
    max_size: int = ...,
    max_queries: int = ...,
    max_inactive_connection_lifetime: float = ...,
    **connect_kwargs: Any,
) -> Pool: ...

async def connect(
    dsn: str | None = None,
    **connect_kwargs: Any,
) -> Connection: ...
