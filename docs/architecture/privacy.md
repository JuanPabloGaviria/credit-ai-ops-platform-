# Privacy and Logging Guardrail

## PII Policy
- Sensitive fields must never be emitted in raw form in logs or audit payloads.
- PII keys are redacted using shared policy from `packages/security/src/security/pii.py`.
- This policy applies to runtime logs, error metadata, and async event diagnostics.

## Forbidden Raw Fields
- `ssn`, `social_security_number`, `government_id`, `tax_id`
- `phone`, `email`, `address`, `full_name`, `date_of_birth`
- `credit_card_number`
