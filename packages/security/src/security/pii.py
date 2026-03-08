"""PII masking utilities enforced across logs and audit records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeGuard, cast

PII_FIELDS = {
    "ssn",
    "social_security_number",
    "government_id",
    "tax_id",
    "phone",
    "email",
    "address",
    "full_name",
    "date_of_birth",
    "credit_card_number",
}


def _is_str_mapping(value: object) -> TypeGuard[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return False
    generic_mapping = cast(Mapping[object, object], value)
    return all(isinstance(key, str) for key in generic_mapping.keys())


def redact_pii(payload: Mapping[str, Any], mask: str = "***REDACTED***") -> dict[str, Any]:
    """Return a redacted copy where sensitive keys are masked recursively."""
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_key = key.lower().strip()
        if normalized_key in PII_FIELDS:
            redacted[key] = mask
        elif _is_str_mapping(value):
            redacted[key] = redact_pii(value, mask=mask)
        elif isinstance(value, list):
            typed_items = cast(list[object], value)
            redacted_list: list[Any] = []
            for item in typed_items:
                if _is_str_mapping(item):
                    redacted_list.append(redact_pii(item, mask=mask))
                else:
                    redacted_list.append(item)
            redacted[key] = redacted_list
        else:
            redacted[key] = value
    return redacted
