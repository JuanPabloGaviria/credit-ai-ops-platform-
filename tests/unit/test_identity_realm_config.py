from __future__ import annotations

import json
from pathlib import Path


def test_keycloak_realm_export_hardens_api_gateway_client() -> None:
    realm_export = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "services"
            / "identity"
            / "config"
            / "realm-export.json"
        ).read_text(encoding="utf-8")
    )

    clients = realm_export["clients"]
    api_gateway_client = next(client for client in clients if client["clientId"] == "api-gateway")

    assert api_gateway_client["publicClient"] is False
    assert api_gateway_client["serviceAccountsEnabled"] is True
    assert api_gateway_client["standardFlowEnabled"] is False
    assert api_gateway_client["implicitFlowEnabled"] is False
    assert api_gateway_client["directAccessGrantsEnabled"] is False
    assert api_gateway_client["redirectUris"] == []
    assert api_gateway_client["webOrigins"] == []
