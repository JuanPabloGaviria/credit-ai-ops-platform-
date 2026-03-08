"""Generic HTTP healthcheck for service containers."""

from __future__ import annotations

import http.client
import os

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_PATH = "/health"
DEFAULT_TIMEOUT_SECONDS = 3.0


def _parse_port(raw_value: str) -> int:
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError("APP_PORT must be an integer") from exc


def _parse_timeout(raw_value: str) -> float:
    try:
        timeout = float(raw_value)
    except ValueError as exc:
        raise RuntimeError("APP_HEALTHCHECK_TIMEOUT_SECONDS must be numeric") from exc
    if timeout <= 0:
        raise RuntimeError("APP_HEALTHCHECK_TIMEOUT_SECONDS must be positive")
    return timeout


def main() -> int:
    host = os.getenv("APP_HEALTHCHECK_HOST", DEFAULT_HOST)
    port = _parse_port(os.getenv("APP_PORT", str(DEFAULT_PORT)))
    path = os.getenv("APP_HEALTHCHECK_PATH", DEFAULT_PATH)
    timeout = _parse_timeout(
        os.getenv("APP_HEALTHCHECK_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    )
    connection = http.client.HTTPConnection(host=host, port=port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        status_code = response.status
        response.read()
    except OSError:
        return 1
    finally:
        connection.close()
    return 0 if 200 <= status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
