"""Decision service async worker consuming score events."""

from __future__ import annotations

import asyncio

from contracts import EVENT_CREDIT_SCORING_GENERATED, QUEUE_DECISION_SCORING_GENERATED
from shared_kernel import build_rabbitmq_client, load_settings

from .repositories import DecisionRepository


async def run_worker() -> None:
    settings = load_settings("decision")
    repository = DecisionRepository(settings)
    consumer = build_rabbitmq_client(settings)

    await repository.connect()
    await consumer.connect()

    await consumer.consume(
        queue_name=QUEUE_DECISION_SCORING_GENERATED,
        routing_keys=[EVENT_CREDIT_SCORING_GENERATED],
        handler=repository.handle_score_event,
    )

    try:
        await asyncio.Event().wait()
    finally:
        await consumer.close()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
