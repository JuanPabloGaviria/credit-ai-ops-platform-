"""Outbox relay worker for decoupled event publication."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from contracts import EventEnvelope

from .database import DatabaseExecutor
from .errors import ServiceError
from .outbox import (
    fetch_pending_outbox_events,
    mark_outbox_event_failed,
    mark_outbox_event_published,
)

EventPublisher = Callable[[EventEnvelope], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OutboxRelayConfig:
    """Config for relay poll and batch behavior."""

    outbox_table: str
    operation_prefix: str
    batch_size: int
    poll_interval_seconds: float
    claim_lease_seconds: int
    max_publish_attempts: int


class OutboxRelayWorker:
    """Poll outbox rows and publish them through broker transport."""

    def __init__(
        self,
        *,
        db: DatabaseExecutor,
        publish_event: EventPublisher,
        config: OutboxRelayConfig,
    ) -> None:
        self._db = db
        self._publish_event = publish_event
        self._config = config

    async def relay_once(self) -> int:
        """Publish one batch and mark successful events as published."""
        events = await fetch_pending_outbox_events(
            self._db,
            self._config.outbox_table,
            limit=self._config.batch_size,
            lease_seconds=self._config.claim_lease_seconds,
        )
        published = 0
        for claimed_event in events:
            try:
                await self._publish_event(claimed_event.event)
            except ServiceError:
                await mark_outbox_event_failed(
                    self._db,
                    self._config.outbox_table,
                    claimed_event.event.event_id,
                    claim_token=claimed_event.claim_token,
                    error_message="relay_publish_service_error",
                    max_attempts=self._config.max_publish_attempts,
                )
                raise
            except Exception as exc:
                await mark_outbox_event_failed(
                    self._db,
                    self._config.outbox_table,
                    claimed_event.event.event_id,
                    claim_token=claimed_event.claim_token,
                    error_message=str(exc),
                    max_attempts=self._config.max_publish_attempts,
                )
                raise ServiceError(
                    error_code="OUTBOX_RELAY_PUBLISH_FAILED",
                    message="Outbox relay failed to publish event",
                    operation=f"{self._config.operation_prefix}_publish",
                    status_code=503,
                    cause=str(exc),
                    hint=f"Inspect broker health for table '{self._config.outbox_table}'",
                ) from exc

            await mark_outbox_event_published(
                self._db,
                self._config.outbox_table,
                claimed_event.event.event_id,
                claim_token=claimed_event.claim_token,
            )
            published += 1
        return published

    async def run_forever(self) -> None:
        """Continuously relay outbox batches at configured poll interval."""
        while True:
            _ = await self.relay_once()
            await asyncio.sleep(self._config.poll_interval_seconds)
