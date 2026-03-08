variable "project_name" {
  description = "Project base name used for Azure resource naming."
  type        = string
  default     = "credit-ai-ops"
}

variable "resource_group_name" {
  description = "Azure resource group for the Container Apps platform."
  type        = string
  default     = "credit-ai-ops-rg"
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "eastus"
}

variable "environment" {
  description = "Runtime environment flag passed to services."
  type        = string
  default     = "prod"
  validation {
    condition     = contains(["local", "dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of local, dev, staging, or prod."
  }
}

variable "app_version" {
  description = "Application version label injected into each workload."
  type        = string
  default     = "0.1.0"
}

variable "log_level" {
  description = "Service log level."
  type        = string
  default     = "INFO"
}

variable "container_registry_server" {
  description = "Container registry server hostname, for example ghcr.io."
  type        = string
  default     = "ghcr.io"
}

variable "container_registry_username" {
  description = "Registry username used by Container Apps."
  type        = string
  default     = null
  nullable    = true
}

variable "container_registry_password" {
  description = "Registry password or token used by Container Apps."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "service_image_references" {
  description = "Per-service immutable OCI references pinned by digest."
  type        = map(string)
  validation {
    condition = (
      length(
        setsubtract(
          toset([
            "api-gateway",
            "application",
            "feature",
            "scoring",
            "decision",
            "collab-assistant",
            "mlops",
            "observability-audit",
          ]),
          toset(keys(var.service_image_references)),
        )
      ) == 0 &&
      length(
        setsubtract(
          toset(keys(var.service_image_references)),
          toset([
            "api-gateway",
            "application",
            "feature",
            "scoring",
            "decision",
            "collab-assistant",
            "mlops",
            "observability-audit",
          ]),
        )
      ) == 0
    )
    error_message = "service_image_references must define exactly api-gateway, application, feature, scoring, decision, collab-assistant, mlops, and observability-audit."
  }
  validation {
    condition = alltrue([
      for image_name in [
        "api-gateway",
        "application",
        "feature",
        "scoring",
        "decision",
        "collab-assistant",
        "mlops",
        "observability-audit",
        ] : can(regex(
          "^[^\\s]+@sha256:[0-9a-f]{64}$",
          lookup(var.service_image_references, image_name, ""),
      ))
    ])
    error_message = "service_image_references values must be immutable OCI references pinned by sha256 digest."
  }
}

variable "container_registry_use_managed_identity" {
  description = "Use a managed identity for Azure Container Registry pulls instead of username/password credentials."
  type        = bool
  default     = false
}

variable "container_registry_resource_id" {
  description = "Azure resource ID for the Container Registry when managed identity pull is enabled."
  type        = string
  default     = null
  nullable    = true
}

variable "key_vault_id" {
  description = "Azure Key Vault resource ID used for runtime secret references."
  type        = string
  default     = null
  nullable    = true
}

variable "key_vault_secret_ids" {
  description = "Versionless Key Vault secret IDs keyed by runtime secret name."
  type        = map(string)
  default     = {}
  validation {
    condition = length(
      setsubtract(
        toset(keys(var.key_vault_secret_ids)),
        toset([
          "postgres-dsn",
          "rabbitmq-url",
          "auth-service-client-secret",
          "auth-shared-secret",
          "model-signing-private-key-pem",
          "model-signing-public-key-pem",
          "registry-password",
        ]),
      )
    ) == 0
    error_message = "key_vault_secret_ids may only define postgres-dsn, rabbitmq-url, auth-service-client-secret, auth-shared-secret, model-signing-private-key-pem, model-signing-public-key-pem, and registry-password."
  }
}

variable "postgres_dsn" {
  description = "Managed PostgreSQL DSN injected into all workloads outside Key Vault-backed environments."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "rabbitmq_url" {
  description = "Managed RabbitMQ URL injected into all workloads outside Key Vault-backed environments."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "auth_mode" {
  description = "Service auth mode."
  type        = string
  validation {
    condition     = contains(["disabled", "required"], var.auth_mode)
    error_message = "auth_mode must be disabled or required."
  }
}

variable "auth_issuer" {
  description = "OIDC issuer URL."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_jwks_url" {
  description = "JWKS endpoint URL."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_required_scope" {
  description = "OAuth scope required on protected endpoints."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_required_audience" {
  description = "Required token audience."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_clock_skew_seconds" {
  description = "Clock skew allowance for token validation."
  type        = number
  default     = 60
}

variable "auth_service_token_url" {
  description = "Client-credentials token URL for service-to-service calls."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_service_client_id" {
  description = "Client ID for service-to-service tokens."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_service_client_secret" {
  description = "Client secret for service-to-service tokens."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "auth_service_scope" {
  description = "Requested scope for service-to-service tokens."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_service_audience" {
  description = "Requested audience for service-to-service tokens."
  type        = string
  default     = null
  nullable    = true
}

variable "auth_shared_secret" {
  description = "Optional shared-secret fallback for local or emergency auth."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "model_signing_private_key_pem" {
  description = "Ed25519 private key used by mlops-service to sign promoted model packages outside Key Vault-backed environments."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "model_signing_public_key_pem" {
  description = "Ed25519 public key used by scoring-service to verify promoted model packages outside Key Vault-backed environments."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "scoring_model_name" {
  description = "Promoted scoring model name."
  type        = string
  default     = "credit-risk"
}

variable "scoring_model_stage" {
  description = "Promoted scoring stage to serve."
  type        = string
  default     = "production"
  validation {
    condition     = contains(["staging", "production"], var.scoring_model_stage)
    error_message = "scoring_model_stage must be staging or production."
  }
}

variable "enable_llm" {
  description = "Enable LLM-backed assistant behavior."
  type        = bool
  default     = false
}

variable "enable_pii_logging" {
  description = "Enable PII logging. Keep disabled in regulated deployments."
  type        = bool
  default     = false
}

variable "skip_startup_dependency_checks" {
  description = "Skip runtime dependency probes during startup."
  type        = bool
  default     = false
}

variable "otel_enabled" {
  description = "Enable OpenTelemetry trace export for all workloads."
  type        = bool
  default     = true
}

variable "otel_service_namespace" {
  description = "Shared OpenTelemetry service namespace."
  type        = string
  default     = "credit-ai-ops"
}

variable "otel_sampler_ratio" {
  description = "Trace sampling ratio enforced by the workload SDKs."
  type        = number
  default     = 1.0
  validation {
    condition     = var.otel_sampler_ratio >= 0 && var.otel_sampler_ratio <= 1
    error_message = "otel_sampler_ratio must be between 0 and 1."
  }
}

variable "artifact_storage_account_name" {
  description = "Globally unique Storage Account name for model artifacts."
  type        = string
  default     = "creditaiopsartifacts"
  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.artifact_storage_account_name))
    error_message = "artifact_storage_account_name must be 3-24 lowercase alphanumeric characters."
  }
}

variable "artifact_storage_account_replication_type" {
  description = "Replication type for the artifact storage account."
  type        = string
  default     = "ZRS"
  validation {
    condition     = contains(["LRS", "GRS", "RAGRS", "ZRS", "GZRS", "RAGZRS"], var.artifact_storage_account_replication_type)
    error_message = "artifact_storage_account_replication_type must be a valid Azure replication type."
  }
}

variable "artifact_container_name" {
  description = "Private Blob container that stores model artifacts and model cards."
  type        = string
  default     = "mlops-artifacts"
  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$", var.artifact_container_name))
    error_message = "artifact_container_name must be a valid Azure Blob container name."
  }
}

variable "infrastructure_subnet_id" {
  description = "Delegated subnet ID for private Container Apps environments."
  type        = string
  default     = null
  nullable    = true
}

variable "infrastructure_resource_group_name" {
  description = "Optional infrastructure resource group name used by the managed environment."
  type        = string
  default     = null
  nullable    = true
}

variable "container_app_environment_public_network_access" {
  description = "Override for Container Apps environment public network access."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = (
      var.container_app_environment_public_network_access == null ||
      contains(["Enabled", "Disabled"], var.container_app_environment_public_network_access)
    )
    error_message = "container_app_environment_public_network_access must be Enabled, Disabled, or null."
  }
}

variable "zone_redundancy_enabled" {
  description = "Enable zone redundancy when the environment uses a dedicated subnet."
  type        = bool
  default     = false
}

variable "gateway_external_ingress_enabled" {
  description = "Expose the gateway through Container Apps ingress."
  type        = bool
  default     = true
}

variable "gateway_allowed_cidrs" {
  description = "Optional allowlist of CIDR ranges for gateway ingress."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags applied to all Azure resources."
  type        = map(string)
  default     = {}
}
