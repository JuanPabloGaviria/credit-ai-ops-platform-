# Identity Service Integration (Keycloak)

This repository treats identity as OIDC integration with Keycloak as the IdP.
No custom identity provider is implemented.

## Responsibilities
- Realm and client bootstrap configuration
- Role and claim mapping contract
- Service-to-service token validation profile

The exported `api-gateway` client is intentionally configured as a confidential
service-account client:

- `serviceAccountsEnabled=true`
- browser redirect flows disabled
- no wildcard redirect URIs

See `infra/docker/docker-compose.yml` and `infra/container-apps/keycloak.md` for runtime details.
