# Gateway Auth Runbook

## Scope
OIDC token validation is enforced at `api-gateway` for protected write endpoints.

Current protected endpoint:
- `POST /v1/gateway/credit-evaluate`

## Modes
- `AUTH_MODE=disabled`: auth check bypassed (local scaffolding/default)
- `AUTH_MODE=required`: bearer token required and validated via issuer + JWKS

When `AUTH_MODE=required`, gateway startup fails fast if:
- issuer/JWKS or shared-secret config is incomplete
- JWKS or token endpoint is unreachable

## Required Env (when `AUTH_MODE=required`)
- Primary auth inputs:
  - `AUTH_ISSUER`
  - `AUTH_JWKS_URL`
  - `AUTH_REQUIRED_SCOPE`
  - `AUTH_REQUIRED_AUDIENCE`
- Service-to-service token inputs:
  - `AUTH_SERVICE_TOKEN_URL`
  - `AUTH_SERVICE_CLIENT_ID`
  - `AUTH_SERVICE_CLIENT_SECRET`

Optional:
- `AUTH_SERVICE_SCOPE`
- `AUTH_SERVICE_AUDIENCE`
- `AUTH_CLOCK_SKEW_SECONDS`
- `KEYCLOAK_REQUEST_TIMEOUT_SECONDS`

Keycloak compatibility inputs are still accepted for local development and are translated into the
primary auth settings when `AUTH_*` values are omitted:

- `KEYCLOAK_URL` + `KEYCLOAK_REALM` -> `AUTH_ISSUER`, `AUTH_JWKS_URL`, `AUTH_SERVICE_TOKEN_URL`
- `KEYCLOAK_CLIENT_ID` -> `AUTH_SERVICE_CLIENT_ID`
- `KEYCLOAK_CLIENT_SECRET` -> `AUTH_SERVICE_CLIENT_SECRET`
- `KEYCLOAK_REQUIRED_SCOPE` -> `AUTH_REQUIRED_SCOPE`
- `KEYCLOAK_REQUIRED_AUDIENCE` -> `AUTH_REQUIRED_AUDIENCE`

## Failure Envelopes
- `AUTH_MISSING_BEARER_TOKEN` -> 401
- `AUTH_INVALID_AUTH_SCHEME` -> 401
- `AUTH_INVALID_TOKEN` -> 401
- `AUTH_FORBIDDEN_SCOPE` -> 403
- `AUTH_FORBIDDEN_AUDIENCE` -> 403
- `AUTH_PROVIDER_UNAVAILABLE` -> 503
- `AUTH_PROVIDER_RESPONSE_INVALID` -> 502
- `AUTH_CONFIG_INVALID` -> 500
- `AUTH_PROVIDER_INVALID_URL` -> 500
- `AUTH_PROVIDER_UNREACHABLE` -> 503

## Local Validation Example
```bash
source .venv/bin/activate
export AUTH_MODE=required
export KEYCLOAK_URL=http://localhost:8080
export KEYCLOAK_REALM=credit-ai-ops
export KEYCLOAK_CLIENT_ID=api-gateway
export KEYCLOAK_CLIENT_SECRET=replace-me
export KEYCLOAK_REQUIRED_SCOPE=credit_analyst
export KEYCLOAK_REQUIRED_AUDIENCE=api-gateway
pytest tests/unit/test_gateway_auth.py -vv
```
