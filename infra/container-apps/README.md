# Azure Container Apps Deployment

The Container Apps path now covers the production topology instead of only the shell:

- gateway ingress workload
- internal API workloads for domain and governance services
- dedicated broker-consumer worker workloads
- dedicated outbox relay workloads
- managed-identity Blob artifact access for MLOps and promoted scoring runtime
- workspace-backed Application Insights trace export through the managed Container Apps OpenTelemetry agent

Managed dependencies still supplied outside this module:

- PostgreSQL and RabbitMQ endpoints
- OIDC issuer, JWKS, and client-credentials values
- Azure Key Vault secret provisioning for DSNs, auth credentials, and model-signing keys
- per-service immutable image digests

Production posture now expects:

- delegated subnet for the managed environment
- Container Apps environment public network access disabled
- Key Vault-backed runtime secrets via managed identity
- OIDC auth required
- PII logging disabled
- OpenTelemetry enabled with traces exported to Application Insights
- application and platform logs retained in Log Analytics
- gateway CIDR allowlist when external ingress is enabled
- Key Vault-backed `MODEL_SIGNING_PRIVATE_KEY_PEM` for `mlops` and `MODEL_SIGNING_PUBLIC_KEY_PEM` for `scoring`
- Azure Container Registry managed identity pull when `container_registry_use_managed_identity=true`, otherwise Key Vault-backed registry password reference

AKS remains documented as a next-stage target in `infra/terraform/aks/README.md`.
