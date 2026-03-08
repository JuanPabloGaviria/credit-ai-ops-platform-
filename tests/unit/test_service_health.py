import pytest
from api_gateway.main import app
from fastapi.testclient import TestClient


@pytest.mark.unit
def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
