"""Collaborator assistant worker consuming decision events."""

from __future__ import annotations

import asyncio

from contracts import EVENT_CREDIT_DECISION_MADE, QUEUE_ASSISTANT_DECISION_MADE
from shared_kernel import build_rabbitmq_client, load_settings

from .repositories import AssistantRepository


async def run_worker() -> None:
    settings = load_settings("collab-assistant")
    repository = AssistantRepository(settings)
    consumer = build_rabbitmq_client(settings)

    await repository.connect()
    await consumer.connect()

    await consumer.consume(
        queue_name=QUEUE_ASSISTANT_DECISION_MADE,
        routing_keys=[EVENT_CREDIT_DECISION_MADE],
        handler=repository.handle_decision_event,
    )

    try:
        await asyncio.Event().wait()
    finally:
        await consumer.close()
        await repository.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
