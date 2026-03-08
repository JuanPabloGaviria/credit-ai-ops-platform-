"""Enforce hardened container build policy for all service images."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = ROOT / "services"
DOCKERIGNORE_PATH = ROOT / ".dockerignore"

REQUIRED_BASE_IMAGE = (
    "python:3.11.11-slim-bookworm@sha256:081075da77b2b55c23c088251026fb69a7b2bf92471e491ff5fd75c192fd38e5"
)
REQUIRED_DOCKERIGNORE_PATTERNS = (
    ".git",
    ".github",
    ".venv",
    "__pycache__/",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "tests",
)
REQUIRED_DOCKERFILE_PATTERNS = (
    f"ARG PYTHON_BASE_IMAGE={REQUIRED_BASE_IMAGE}",
    "FROM ${PYTHON_BASE_IMAGE} AS builder",
    "FROM ${PYTHON_BASE_IMAGE} AS runtime",
    "RUN python -m venv /opt/venv",
    "pip install --require-hashes -r /app/requirements/base.lock",
    "COPY --from=builder --chown=app:app /opt/venv /opt/venv",
    "COPY --chown=app:app packages /app/packages",
    "COPY --chown=app:app scripts/docker /app/scripts/docker",
    "COPY --chown=app:app scripts/runtime /app/scripts/runtime",
    'ENTRYPOINT ["python", "/app/scripts/docker/run_service.py"]',
    (
        "HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 "
        'CMD ["python", "/app/scripts/docker/healthcheck.py"]'
    ),
    "USER 10001:app",
)
FORBIDDEN_DOCKERFILE_PATTERNS = (
    'CMD ["sh", "-c"',
    'ENTRYPOINT ["sh", "-c"',
    "RUN pip install --no-cache-dir -r /app/requirements/base.lock",
)


def _print_ok(message: str) -> None:
    print(f"[container-gate] OK {message}")


def _print_fail(message: str) -> None:
    print(f"[container-gate] FAIL {message}")


def _check_dockerignore(failures: list[str]) -> None:
    try:
        contents = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(f"unable to read .dockerignore: {exc}")
        return

    dockerignore_lines = contents.splitlines()
    missing_patterns = [
        pattern for pattern in REQUIRED_DOCKERIGNORE_PATTERNS if pattern not in dockerignore_lines
    ]
    if missing_patterns:
        failures.append(
            ".dockerignore is missing required exclusions: " + ", ".join(sorted(missing_patterns))
        )
        return
    _print_ok(".dockerignore excludes build, cache, and test noise")


def _check_dockerfile(path: Path, failures: list[str]) -> None:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(f"{path.relative_to(ROOT)} could not be read: {exc}")
        return

    if contents.count("FROM ") != 2:
        failures.append(f"{path.relative_to(ROOT)} must define exactly two build stages")

    for pattern in REQUIRED_DOCKERFILE_PATTERNS:
        if pattern not in contents:
            failures.append(f"{path.relative_to(ROOT)} is missing required pattern: {pattern}")

    for pattern in FORBIDDEN_DOCKERFILE_PATTERNS:
        if pattern in contents:
            failures.append(f"{path.relative_to(ROOT)} contains forbidden pattern: {pattern}")

    if "EXPOSE 8000" not in contents:
        failures.append(f"{path.relative_to(ROOT)} must expose port 8000")

    if "APP_MODULE=" not in contents:
        failures.append(f"{path.relative_to(ROOT)} must define APP_MODULE")

    if "groupadd --system app" not in contents or "useradd --system" not in contents:
        failures.append(f"{path.relative_to(ROOT)} must create a dedicated non-root runtime user")


def main() -> int:
    failures: list[str] = []

    dockerfiles = sorted(SERVICES_DIR.glob("*/Dockerfile"))
    if not dockerfiles:
        failures.append("no service Dockerfiles found under services/*/Dockerfile")

    _check_dockerignore(failures)
    for dockerfile in dockerfiles:
        _check_dockerfile(dockerfile, failures)

    if failures:
        for failure in failures:
            _print_fail(failure)
        return 1

    _print_ok(f"validated {len(dockerfiles)} hardened service Dockerfiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
