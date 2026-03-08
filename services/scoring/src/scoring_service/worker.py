"""Scoring service async worker consuming feature events."""

from __future__ import annotations

import asyncio

from contracts import EVENT_CREDIT_FEATURE_MATERIALIZED, QUEUE_SCORING_FEATURE_MATERIALIZED
from shared_kernel import build_rabbitmq_client, load_settings

from .repositories import ScoringRepository


async def run_worker() -> None:
    settings = load_settings("scoring")
    repository = ScoringRepository(settings)
    consumer = build_rabbitmq_client(settings)

    await repository.connect()
    await consumer.connect()

    await consumer.consume(
        queue_name=QUEUE_SCORING_FEATURE_MATERIALIZED,
        routing_keys=[EVENT_CREDIT_FEATURE_MATERIALIZED],
        handler=repository.handle_feature_event,
    )

    try:
        await asyncio.Event().wait()
    finally:
        await consumer.close()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
