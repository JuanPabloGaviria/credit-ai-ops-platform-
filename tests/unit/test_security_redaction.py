import pytest

from security import redact_pii


@pytest.mark.unit
def test_redact_pii_masks_sensitive_keys() -> None:
    payload = {
        "full_name": "Ada Lovelace",
        "nested": {"email": "ada@example.com", "risk": "low"},
        "safe": "ok",
    }

    redacted = redact_pii(payload)

    assert redacted["full_name"] == "***REDACTED***"
    assert redacted["nested"]["email"] == "***REDACTED***"
    assert redacted["nested"]["risk"] == "low"
    assert redacted["safe"] == "ok"
