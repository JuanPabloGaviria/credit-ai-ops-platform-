from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from urllib.parse import urlparse

from shared_kernel import DatabaseClient, load_settings
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_PATTERN = "services/*/migrations/*.sql"
_APPLICATION_COUNT_QUERIES: dict[str, str] = {
    "application_submissions": (
        "SELECT COUNT(*) AS count FROM application_submissions WHERE application_id = $1"
    ),
    "feature_vectors": ("SELECT COUNT(*) AS count FROM feature_vectors WHERE application_id = $1"),
    "feature_vector_history": (
        "SELECT COUNT(*) AS count FROM feature_vector_history WHERE application_id = $1"
    ),
    "score_predictions": (
        "SELECT COUNT(*) AS count FROM score_predictions WHERE application_id = $1"
    ),
    "score_prediction_history": (
        "SELECT COUNT(*) AS count FROM score_prediction_history WHERE application_id = $1"
    ),
    "credit_decisions": (
        "SELECT COUNT(*) AS count FROM credit_decisions WHERE application_id = $1"
    ),
    "credit_decision_history": (
        "SELECT COUNT(*) AS count FROM credit_decision_history WHERE application_id = $1"
    ),
    "assistant_summaries": (
        "SELECT COUNT(*) AS count FROM assistant_summaries WHERE application_id = $1"
    ),
    "assistant_summary_history": (
        "SELECT COUNT(*) AS count FROM assistant_summary_history WHERE application_id = $1"
    ),
}
_TRACE_COUNT_QUERIES: dict[str, str] = {
    "audit_events": "SELECT COUNT(*) AS count FROM audit_events WHERE trace_id = $1",
}


def endpoint_reachable(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def integration_ready() -> tuple[bool, str]:
    postgres_dsn = load_settings("application").postgres_dsn or DEFAULT_POSTGRES_DSN
    rabbitmq_url = load_settings("application").rabbitmq_url or DEFAULT_RABBITMQ_URL
    if not endpoint_reachable(postgres_dsn):
        return False, f"Postgres is unreachable for integration test DSN: {postgres_dsn}"
    if not endpoint_reachable(rabbitmq_url):
        return False, f"RabbitMQ is unreachable for integration URL: {rabbitmq_url}"
    return True, "dependencies_reachable"


async def apply_all_migrations(db: DatabaseClient) -> None:
    for migration_path in sorted(ROOT.glob(MIGRATIONS_PATTERN)):
        LOGGER.info("applying_migration=%s", migration_path.relative_to(ROOT))
        await db.execute(migration_path.read_text(encoding="utf-8"))


async def truncate_domain_tables(db: DatabaseClient) -> None:
    await db.execute(
        """
        TRUNCATE TABLE
            ml_evaluation_runs,
            ml_training_runs,
            model_stage_assignments,
            model_registry,
            mlops_outbox,
            application_submissions,
            application_projection_legacy,
            application_outbox,
            application_inbox,
            feature_vector_projection_legacy,
            feature_vector_history,
            feature_outbox,
            feature_inbox,
            score_prediction_projection_legacy,
            score_prediction_history,
            scoring_outbox,
            scoring_inbox,
            credit_decision_history,
            credit_decision_projection_legacy,
            decision_outbox,
            decision_inbox,
            assistant_summary_history,
            assistant_summary_projection_legacy,
            assistant_outbox,
            assistant_inbox,
            audit_events,
            audit_inbox
        """
    )


async def wait_for_application_count(
    *,
    db: DatabaseClient,
    table_name: str,
    application_id: str,
    expected_minimum: int,
    label: str,
    timeout_seconds: float = 15.0,
) -> None:
    query = _APPLICATION_COUNT_QUERIES.get(table_name)
    if query is None:
        raise AssertionError(f"unsupported application count table: {table_name}")
    await _wait_for_count(
        db=db,
        query=query,
        lookup_value=application_id,
        expected_minimum=expected_minimum,
        label=label,
        timeout_seconds=timeout_seconds,
    )


async def wait_for_trace_count(
    *,
    db: DatabaseClient,
    table_name: str,
    trace_id: str,
    expected_minimum: int,
    label: str,
    timeout_seconds: float = 15.0,
) -> None:
    query = _TRACE_COUNT_QUERIES.get(table_name)
    if query is None:
        raise AssertionError(f"unsupported trace count table: {table_name}")
    await _wait_for_count(
        db=db,
        query=query,
        lookup_value=trace_id,
        expected_minimum=expected_minimum,
        label=label,
        timeout_seconds=timeout_seconds,
    )


async def _wait_for_count(
    *,
    db: DatabaseClient,
    query: str,
    lookup_value: str,
    expected_minimum: int,
    label: str,
    timeout_seconds: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_count = 0
    while asyncio.get_running_loop().time() < deadline:
        row = await db.fetchrow(query, lookup_value)
        last_count = int(row["count"]) if row is not None else 0
        if last_count >= expected_minimum:
            LOGGER.info("wait_success label=%s count=%s", label, last_count)
            return
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"{label} timed out after {timeout_seconds}s: expected >= {expected_minimum}, "
        f"observed={last_count}"
    )
