# Audit Reporting Runbook

## Scope
`observability-audit` now supports both write and read/report flows:
- `POST /v1/audit/events`
- `GET /v1/audit/events`
- `GET /v1/audit/events/{event_id}`
- `GET /v1/audit/traces/{trace_id}`

## Query Controls
- `GET /v1/audit/events`
  - optional filters: `event_name`, `trace_id`, `correlation_id`
  - required guardrail: `limit` in `[1, 200]`
- `GET /v1/audit/traces/{trace_id}`
  - trace-scoped list with `limit` in `[1, 500]`

## Returned Context
- read endpoints return `trace_id`, `correlation_id`, and `causation_id` when available
- `correlation_id` is required on persisted event envelopes and is queryable directly
- `causation_id` links downstream events to the source event that triggered them

## Privacy
- PII redaction is applied on write and enforced again on read.
- Sensitive keys are always masked from API responses.

## Failure Signals
- `INVALID_IDEMPOTENCY_KEY` -> malformed optional idempotency header.
- `INVALID_EVENT_NAME` -> event name does not match `<domain>.<entity>.<action>.vN`.
- `AUDIT_EVENT_NOT_FOUND` -> event id does not exist.
- `AUDIT_PAYLOAD_INVALID_JSON` / `AUDIT_PAYLOAD_INVALID_TYPE` -> corrupted stored payload shape.

## Validation
```bash
source .venv/bin/activate
pytest tests/unit/test_observability_audit_routes.py -vv
pytest -m integration -vv
```
