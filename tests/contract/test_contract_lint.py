from pathlib import Path

import pytest


@pytest.mark.contract
def test_contract_assets_exist() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "schemas" / "openapi").exists()
    assert (root / "schemas" / "asyncapi").exists()
    assert (root / "schemas" / "jsonschema").exists()
