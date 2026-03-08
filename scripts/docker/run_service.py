"""Uvicorn launcher for hardened service containers."""

from __future__ import annotations

import os

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"{name} is required")
    return value


def _parse_port(raw_value: str) -> int:
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("APP_PORT must be an integer") from exc
    if port < 1 or port > 65535:
        raise RuntimeError("APP_PORT must be between 1 and 65535")
    return port


def _parse_workers(raw_value: str | None) -> list[str]:
    if raw_value is None or raw_value.strip() == "":
        return []
    try:
        workers = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("UVICORN_WORKERS must be an integer") from exc
    if workers < 1:
        raise RuntimeError("UVICORN_WORKERS must be at least 1")
    return ["--workers", str(workers)]


def main() -> int:
    module_path = _require_env("APP_MODULE")
    host = os.getenv("APP_HOST", DEFAULT_HOST)
    port = _parse_port(os.getenv("APP_PORT", str(DEFAULT_PORT)))
    worker_args = _parse_workers(os.getenv("UVICORN_WORKERS"))
    workers = int(worker_args[1]) if worker_args else None
    uvicorn.run(app=module_path, host=host, port=port, workers=workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
