# Keycloak OIDC Integration Notes

Identity is integrated via Keycloak as an IdP.

- Realm: `credit-ai-ops`
- Primary client: `api-gateway`
- Client posture: confidential service-account client, no browser redirect flows
- Role mapping: `credit_analyst`, `credit_manager`, `ml_engineer`
- Issuer template:
  - `/realms/{realm}`
- JWKS path template:
  - `/realms/{realm}/protocol/openid-connect/certs`
- Service token path template:
  - `/realms/{realm}/protocol/openid-connect/token`
