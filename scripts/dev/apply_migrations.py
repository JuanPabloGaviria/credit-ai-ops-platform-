"""Apply service SQL migrations in deterministic order."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared_kernel import DatabaseClient, ServiceError

ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = ROOT / "services"


def _bootstrap_paths() -> None:
    sys.path.insert(0, str(ROOT / "packages" / "shared-kernel" / "src"))


def _load_shared_kernel() -> tuple[type[DatabaseClient], type[ServiceError]]:
    _bootstrap_paths()
    from shared_kernel import DatabaseClient, ServiceError

    return DatabaseClient, ServiceError


async def _apply_file(db: DatabaseClient, sql_file: Path) -> None:
    sql = sql_file.read_text(encoding="utf-8")
    await db.execute(sql)


async def apply_migrations(postgres_dsn: str, service: str | None) -> int:
    database_client_cls, _ = _load_shared_kernel()
    db = database_client_cls(postgres_dsn)
    await db.connect()
    applied = 0
    try:
        service_dirs = sorted(SERVICES_DIR.glob("*"))
        for service_dir in service_dirs:
            if not service_dir.is_dir():
                continue
            if service is not None and service_dir.name != service:
                continue
            migrations_dir = service_dir / "migrations"
            if not migrations_dir.exists():
                continue
            for sql_file in sorted(migrations_dir.glob("*.sql")):
                await _apply_file(db, sql_file)
                applied += 1
                print(f"applied: {service_dir.name}/{sql_file.name}")
    finally:
        await db.close()
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply SQL migrations")
    parser.add_argument("--service", default=None, help="Optional service directory to scope")
    parser.add_argument(
        "--postgres-dsn",
        required=True,
        help="Postgres DSN for migration execution",
    )
    args = parser.parse_args()

    _, service_error_cls = _load_shared_kernel()
    try:
        applied = asyncio.run(
            apply_migrations(
                postgres_dsn=args.postgres_dsn,
                service=args.service,
            )
        )
    except service_error_cls as exc:
        raise SystemExit(f"migration_failed: {exc.error_code}: {exc.message}") from exc

    print(f"total_applied={applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
