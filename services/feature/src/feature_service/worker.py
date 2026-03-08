"""Feature service async worker consuming application events."""

from __future__ import annotations

import asyncio

from contracts import EVENT_CREDIT_APPLICATION_SUBMITTED, QUEUE_FEATURE_APPLICATION_SUBMITTED
from shared_kernel import build_rabbitmq_client, load_settings

from .repositories import FeatureRepository


async def run_worker() -> None:
    settings = load_settings("feature")
    repository = FeatureRepository(settings)
    consumer = build_rabbitmq_client(settings)

    await repository.connect()
    await consumer.connect()

    await consumer.consume(
        queue_name=QUEUE_FEATURE_APPLICATION_SUBMITTED,
        routing_keys=[EVENT_CREDIT_APPLICATION_SUBMITTED],
        handler=repository.handle_submitted_event,
    )

    try:
        await asyncio.Event().wait()
    finally:
        await consumer.close()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
