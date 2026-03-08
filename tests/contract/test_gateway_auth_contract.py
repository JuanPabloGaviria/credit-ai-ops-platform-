from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_OPENAPI_PATH = ROOT / "schemas" / "openapi" / "api-gateway-v1.yaml"


def _load_openapi_document() -> dict[str, Any]:
    with GATEWAY_OPENAPI_PATH.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    return cast(dict[str, Any], loaded)


@pytest.mark.contract
def test_gateway_credit_evaluate_security_contract() -> None:
    document = _load_openapi_document()
    post_operation = cast(
        dict[str, Any],
        document["paths"]["/v1/gateway/credit-evaluate"]["post"],
    )

    security = cast(list[dict[str, list[str]]], post_operation["security"])
    assert security == [{"bearerAuth": []}]

    security_schemes = cast(dict[str, Any], document["components"]["securitySchemes"])
    bearer_scheme = cast(dict[str, Any], security_schemes["bearerAuth"])
    assert bearer_scheme["type"] == "http"
    assert bearer_scheme["scheme"] == "bearer"


@pytest.mark.contract
def test_gateway_credit_evaluate_auth_response_contract() -> None:
    document = _load_openapi_document()
    post_operation = cast(
        dict[str, Any],
        document["paths"]["/v1/gateway/credit-evaluate"]["post"],
    )
    responses = cast(dict[str, Any], post_operation["responses"])

    for status in ("401", "403", "502", "503"):
        assert status in responses, f"missing auth response status in OpenAPI: {status}"
