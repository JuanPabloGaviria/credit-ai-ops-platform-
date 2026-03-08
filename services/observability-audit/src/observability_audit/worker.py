"""Observability-audit worker consuming all credit domain events."""

from __future__ import annotations

import asyncio

from contracts import QUEUE_AUDIT_CREDIT_EVENTS, ROUTING_CREDIT_ALL
from shared_kernel import build_rabbitmq_client, load_settings

from .repositories import AuditRepository


async def run_worker() -> None:
    settings = load_settings("observability-audit")
    repository = AuditRepository(settings.postgres_dsn)
    consumer = build_rabbitmq_client(settings)

    await repository.connect()
    await consumer.connect()

    await consumer.consume(
        queue_name=QUEUE_AUDIT_CREDIT_EVENTS,
        routing_keys=[ROUTING_CREDIT_ALL],
        handler=repository.handle_event,
    )

    try:
        await asyncio.Event().wait()
    finally:
        await consumer.close()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
