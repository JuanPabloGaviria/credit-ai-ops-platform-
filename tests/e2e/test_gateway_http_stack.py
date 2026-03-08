from __future__ import annotations

import asyncio
import multiprocessing
import os
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn

from shared_kernel import DatabaseClient, ServiceSettings, load_settings
from tests.defaults import (
    DEFAULT_POSTGRES_DSN,
    DEFAULT_RABBITMQ_URL,
    TEST_MODEL_SIGNING_PUBLIC_KEY_PEM,
)
from tests.support.async_chain import (
    apply_all_migrations,
    integration_ready,
    truncate_domain_tables,
    wait_for_application_count,
)
from tests.support.mlops import seed_promoted_scoring_model

ROOT = Path(__file__).resolve().parents[2]
SERVICE_MODULES: dict[str, str] = {
    "feature": "feature_service.main:app",
    "scoring": "scoring_service.main:app",
    "decision": "decision_service.main:app",
    "api-gateway": "api_gateway.main:app",
}


@dataclass(slots=True)
class _RunningService:
    name: str
    base_url: str
    process: BaseProcess
    log_path: Path


@dataclass(frozen=True, slots=True)
class _ServiceLaunchSpec:
    module_path: str
    port: int
    env: dict[str, str]
    log_path: str


def _build_pythonpath() -> str:
    entries = [
        ROOT / "packages" / "shared-kernel" / "src",
        ROOT / "packages" / "contracts" / "src",
        ROOT / "packages" / "observability" / "src",
        ROOT / "packages" / "security" / "src",
        ROOT / "services" / "api-gateway" / "src",
        ROOT / "services" / "application" / "src",
        ROOT / "services" / "feature" / "src",
        ROOT / "services" / "scoring" / "src",
        ROOT / "services" / "decision" / "src",
        ROOT / "services" / "collab-assistant" / "src",
        ROOT / "services" / "mlops" / "src",
        ROOT / "services" / "observability-audit" / "src",
        ROOT,
    ]
    return os.pathsep.join(str(entry) for entry in entries)


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _base_environment(
    *,
    artifact_root_dir: Path,
    feature_url: str,
    scoring_url: str,
    decision_url: str,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": _build_pythonpath(),
            "PYTHONUNBUFFERED": "1",
            "POSTGRES_DSN": DEFAULT_POSTGRES_DSN,
            "RABBITMQ_URL": DEFAULT_RABBITMQ_URL,
            "AUTH_MODE": "disabled",
            "ARTIFACT_STORAGE_BACKEND": "filesystem",
            "ARTIFACT_ROOT_DIR": str(artifact_root_dir),
            "MODEL_SIGNING_PUBLIC_KEY_PEM": TEST_MODEL_SIGNING_PUBLIC_KEY_PEM,
            "FEATURE_SERVICE_URL": feature_url,
            "SCORING_SERVICE_URL": scoring_url,
            "DECISION_SERVICE_URL": decision_url,
        }
    )
    return env


def _log_tail(log_path: Path, *, line_count: int = 40) -> str:
    if not log_path.exists():
        return "<no log output>"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-line_count:])


def _serve_service(spec: _ServiceLaunchSpec) -> None:
    os.environ.update(spec.env)
    with Path(spec.log_path).open("a", encoding="utf-8") as log_handle:
        sys.stdout = log_handle
        sys.stderr = log_handle
        uvicorn.run(
            app=spec.module_path,
            host="127.0.0.1",
            port=spec.port,
            workers=1,
        )


@contextmanager
def _run_service(
    *,
    name: str,
    port: int,
    env: dict[str, str],
    logs_dir: Path,
) -> Iterator[_RunningService]:
    log_path = logs_dir / f"{name}.log"
    log_path.write_text("", encoding="utf-8")
    process = multiprocessing.get_context("spawn").Process(
        target=_serve_service,
        args=(
            _ServiceLaunchSpec(
                module_path=SERVICE_MODULES[name],
                port=port,
                env=env,
                log_path=str(log_path),
            ),
        ),
        name=f"e2e-{name}",
    )
    process.start()
    service = _RunningService(
        name=name,
        base_url=f"http://127.0.0.1:{port}",
        process=process,
        log_path=log_path,
    )
    try:
        _wait_for_ready(service)
        yield service
    finally:
        process.terminate()
        process.join(timeout=10.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)


def _wait_for_ready(service: _RunningService, *, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "service did not become ready"
    while time.monotonic() < deadline:
        return_code = service.process.exitcode
        if return_code is not None:
            raise AssertionError(
                f"{service.name} exited before readiness with code {return_code}\n"
                f"{_log_tail(service.log_path)}"
            )
        try:
            response = httpx.get(f"{service.base_url}/ready", timeout=0.5)
        except httpx.HTTPError as exc:
            last_error = str(exc)
            time.sleep(0.2)
            continue
        if response.status_code == 200:
            return
        last_error = f"status={response.status_code} body={response.text}"
        time.sleep(0.2)
    raise AssertionError(
        f"{service.name} did not become ready within {timeout_seconds:.1f}s: {last_error}\n"
        f"{_log_tail(service.log_path)}"
    )


async def _prepare_gateway_stack(settings_scoring: ServiceSettings) -> None:
    db = DatabaseClient(DEFAULT_POSTGRES_DSN)
    await db.connect()
    try:
        await apply_all_migrations(db)
        await truncate_domain_tables(db)
    finally:
        await db.close()

    await seed_promoted_scoring_model(
        settings_scoring=settings_scoring,
        trace_id=f"trace-e2e-seed-{uuid4().hex}",
    )


async def _assert_single_history_projection(application_id: str) -> None:
    db = DatabaseClient(DEFAULT_POSTGRES_DSN)
    await db.connect()
    try:
        await wait_for_application_count(
            db=db,
            table_name="feature_vector_history",
            application_id=application_id,
            expected_minimum=1,
            label="feature_history_recorded",
        )
        await wait_for_application_count(
            db=db,
            table_name="score_prediction_history",
            application_id=application_id,
            expected_minimum=1,
            label="scoring_history_recorded",
        )
        await wait_for_application_count(
            db=db,
            table_name="credit_decision_history",
            application_id=application_id,
            expected_minimum=1,
            label="decision_history_recorded",
        )
        feature_history = await db.fetchrow(
            "SELECT COUNT(*) AS count FROM feature_vector_history WHERE application_id = $1",
            application_id,
        )
        scoring_history = await db.fetchrow(
            "SELECT COUNT(*) AS count FROM score_prediction_history WHERE application_id = $1",
            application_id,
        )
        decision_history = await db.fetchrow(
            "SELECT COUNT(*) AS count FROM credit_decision_history WHERE application_id = $1",
            application_id,
        )
    finally:
        await db.close()

    assert feature_history is not None and int(feature_history["count"]) == 1
    assert scoring_history is not None and int(scoring_history["count"]) == 1
    assert decision_history is not None and int(decision_history["count"]) == 1


@pytest.mark.e2e
def test_gateway_http_stack_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ready, reason = integration_ready()
    if not ready:
        pytest.skip(reason)

    gateway_port = _reserve_local_port()
    feature_port = _reserve_local_port()
    scoring_port = _reserve_local_port()
    decision_port = _reserve_local_port()
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    feature_url = f"http://127.0.0.1:{feature_port}"
    scoring_url = f"http://127.0.0.1:{scoring_port}"
    decision_url = f"http://127.0.0.1:{decision_port}"
    artifact_root_dir = tmp_path / "artifacts"
    artifact_root_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = tmp_path / "service-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)
    monkeypatch.setenv("RABBITMQ_URL", DEFAULT_RABBITMQ_URL)
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("ARTIFACT_STORAGE_BACKEND", "filesystem")
    monkeypatch.setenv("ARTIFACT_ROOT_DIR", str(artifact_root_dir))
    monkeypatch.setenv("MODEL_SIGNING_PUBLIC_KEY_PEM", TEST_MODEL_SIGNING_PUBLIC_KEY_PEM)

    settings_scoring = load_settings("scoring")
    asyncio.run(_prepare_gateway_stack(settings_scoring))

    base_env = _base_environment(
        artifact_root_dir=artifact_root_dir,
        feature_url=feature_url,
        scoring_url=scoring_url,
        decision_url=decision_url,
    )

    with ExitStack() as stack:
        _ = stack.enter_context(
            _run_service(name="feature", port=feature_port, env=base_env, logs_dir=logs_dir)
        )
        _ = stack.enter_context(
            _run_service(name="scoring", port=scoring_port, env=base_env, logs_dir=logs_dir)
        )
        _ = stack.enter_context(
            _run_service(name="decision", port=decision_port, env=base_env, logs_dir=logs_dir)
        )
        _ = stack.enter_context(
            _run_service(name="api-gateway", port=gateway_port, env=base_env, logs_dir=logs_dir)
        )

        application_id = f"app-e2e-{uuid4().hex[:12]}"
        request_headers = {"x-idempotency-key": f"idem-e2e-{uuid4().hex}"}
        request_payload = {
            "application_id": application_id,
            "applicant_id": f"applicant-{uuid4().hex[:12]}",
            "monthly_income": 5000.0,
            "monthly_debt": 1800.0,
            "requested_amount": 20000.0,
            "credit_history_months": 36,
            "existing_defaults": 0,
        }

        with httpx.Client(base_url=gateway_url, timeout=10.0) as client:
            health = client.get("/health")
            assert health.status_code == 200

            first = client.post(
                "/v1/gateway/credit-evaluate",
                json=request_payload,
                headers=request_headers,
            )
            assert first.status_code == 200, first.text
            first_payload = first.json()

            replay = client.post(
                "/v1/gateway/credit-evaluate",
                json=request_payload,
                headers=request_headers,
            )
            assert replay.status_code == 200, replay.text
            assert replay.json() == first_payload

        assert first_payload["features"]["application_id"] == application_id
        assert first_payload["score"]["application_id"] == application_id
        assert first_payload["decision"]["application_id"] == application_id
        assert first_payload["decision"]["decision"] in {"approve", "review", "decline"}

        asyncio.run(_assert_single_history_projection(application_id))
