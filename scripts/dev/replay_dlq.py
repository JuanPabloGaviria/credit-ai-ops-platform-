"""Replay messages from a RabbitMQ DLQ back to main topic exchange."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared_kernel import RabbitMQClient

ROOT = Path(__file__).resolve().parents[2]


def _bootstrap_paths() -> None:
    sys.path.insert(0, str(ROOT / "packages" / "shared-kernel" / "src"))
    sys.path.insert(0, str(ROOT / "packages" / "contracts" / "src"))


def _load_rabbitmq_client() -> type[RabbitMQClient]:
    _bootstrap_paths()
    from shared_kernel import RabbitMQClient

    return RabbitMQClient


async def _run(queue_name: str, limit: int, rabbitmq_url: str) -> int:
    rabbitmq_client_cls = _load_rabbitmq_client()
    client = rabbitmq_client_cls(rabbitmq_url)
    await client.connect()
    try:
        replayed = await client.replay_dead_letter_queue(queue_name=queue_name, limit=limit)
    finally:
        await client.close()
    return replayed


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay RabbitMQ dead-letter queue messages")
    parser.add_argument("queue_name", help="Primary queue name (without .dlq suffix)")
    parser.add_argument("--limit", type=int, default=100, help="Maximum messages to replay")
    parser.add_argument("--rabbitmq-url", required=True, help="RabbitMQ connection URL")
    args = parser.parse_args()

    replayed = asyncio.run(
        _run(
            queue_name=args.queue_name,
            limit=args.limit,
            rabbitmq_url=args.rabbitmq_url,
        )
    )
    print(f"replayed_messages={replayed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
