"""Run an explicit outbox relay worker for a selected service."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parents[2]
_OUTBOX_TABLE_BY_SERVICE: dict[str, str] = {
    "application": "application_outbox",
    "feature": "feature_outbox",
    "scoring": "scoring_outbox",
    "decision": "decision_outbox",
    "collab-assistant": "assistant_outbox",
    "mlops": "mlops_outbox",
}


def _bootstrap_paths() -> None:
    for package_name in ("shared-kernel", "contracts", "observability", "security"):
        sys.path.insert(0, str(ROOT / "packages" / package_name / "src"))


_bootstrap_paths()


class _Logger(Protocol):
    def info(self, event: str, /, **kwargs: object) -> None: ...


async def run_relay(service: str, once: bool) -> None:
    from shared_kernel import (
        DatabaseClient,
        OutboxRelayConfig,
        OutboxRelayWorker,
        build_rabbitmq_client,
        load_settings,
    )
    from shared_kernel.logging import configure_logging, get_logger

    settings = load_settings(service)
    configure_logging(settings.log_level)
    logger = cast(_Logger, get_logger(f"{service}-outbox-relay"))
    outbox_table = _OUTBOX_TABLE_BY_SERVICE[service]

    db = DatabaseClient(settings.postgres_dsn)
    broker = build_rabbitmq_client(settings)
    worker = OutboxRelayWorker(
        db=db,
        publish_event=broker.publish_event,
        config=OutboxRelayConfig(
            outbox_table=outbox_table,
            operation_prefix=f"{service}_outbox_relay",
            batch_size=settings.outbox_relay_batch_size,
            poll_interval_seconds=settings.outbox_relay_poll_interval_seconds,
            claim_lease_seconds=settings.outbox_relay_claim_lease_seconds,
            max_publish_attempts=settings.outbox_relay_max_publish_attempts,
        ),
    )

    await db.connect()
    await broker.connect()
    try:
        if once:
            published = await worker.relay_once()
            logger.info(
                "outbox_relay_once_complete",
                service=service,
                outbox_table=outbox_table,
                published_events=published,
            )
            return

        stop_event = asyncio.Event()

        def _stop_handler(received_signal: int, frame: FrameType | None) -> None:
            _ = received_signal
            _ = frame
            stop_event.set()

        signal.signal(signal.SIGINT, _stop_handler)
        signal.signal(signal.SIGTERM, _stop_handler)

        while not stop_event.is_set():
            published = await worker.relay_once()
            logger.info(
                "outbox_relay_cycle_complete",
                service=service,
                outbox_table=outbox_table,
                published_events=published,
            )
            await asyncio.sleep(settings.outbox_relay_poll_interval_seconds)
    finally:
        await broker.close()
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run outbox relay worker")
    parser.add_argument(
        "--service",
        required=True,
        choices=sorted(_OUTBOX_TABLE_BY_SERVICE.keys()),
        help="Service name whose outbox table will be relayed",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll/publish cycle and exit",
    )
    args = parser.parse_args()
    from shared_kernel import ServiceError

    try:
        asyncio.run(run_relay(service=args.service, once=args.once))
    except ServiceError as exc:
        raise SystemExit(f"outbox_relay_failed: {exc.error_code}: {exc.message}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
