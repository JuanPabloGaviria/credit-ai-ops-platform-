from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from .abc import AbstractConnection

class ExchangeType(str, Enum):
    TOPIC = "topic"

class DeliveryMode(int, Enum):
    PERSISTENT = 2

class Message:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str | None = ...,
        delivery_mode: int | DeliveryMode | None = ...,
        message_id: str | None = ...,
        timestamp: datetime | None = ...,
        headers: dict[str, Any] | None = ...,
    ) -> None: ...

async def connect_robust(
    url: str,
    *args: Any,
    **kwargs: Any,
) -> AbstractConnection: ...
