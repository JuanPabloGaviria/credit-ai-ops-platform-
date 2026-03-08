terraform {
  required_version = ">= 1.8.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.1"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.1"
    }
  }
}

provider "azurerm" {
  features {}
}

locals {
  required_service_images = [
    "api-gateway",
    "application",
    "feature",
    "scoring",
    "decision",
    "collab-assistant",
    "mlops",
    "observability-audit",
  ]
  allowed_secret_names = [
    "postgres-dsn",
    "rabbitmq-url",
    "auth-service-client-secret",
    "auth-shared-secret",
    "model-signing-private-key-pem",
    "model-signing-public-key-pem",
    "registry-password",
  ]

  tags = merge(
    {
      system       = "credit-ai-ops"
      managed-by   = "terraform"
      platform     = "azure-container-apps"
      repo         = "credit-ai-ops-platform"
      review-grade = "bank"
    },
    var.tags,
  )

  registry_server = trimsuffix(var.container_registry_server, "/")
  artifact_blob_account_url = format(
    "https://%s.blob.core.windows.net",
    var.artifact_storage_account_name,
  )
  service_image_references = {
    for image_name in local.required_service_images :
    image_name => var.service_image_references[image_name]
  }
  key_vault_secret_ids = {
    for name, secret_id in var.key_vault_secret_ids :
    name => trimspace(secret_id)
    if trimspace(secret_id) != ""
  }
  direct_secret_values = {
    "postgres-dsn"                  = var.postgres_dsn
    "rabbitmq-url"                  = var.rabbitmq_url
    "auth-service-client-secret"    = var.auth_service_client_secret
    "auth-shared-secret"            = var.auth_shared_secret
    "model-signing-private-key-pem" = var.model_signing_private_key_pem
    "model-signing-public-key-pem"  = var.model_signing_public_key_pem
    "registry-password"             = var.container_registry_use_managed_identity ? null : var.container_registry_password
  }
  secret_available = {
    for name in local.allowed_secret_names :
    name => (
      lookup(local.key_vault_secret_ids, name, null) != null ||
      lookup(local.direct_secret_values, name, null) != null
    )
  }
  raw_secret_names = [
    for name in local.allowed_secret_names :
    name
    if(
      lookup(local.direct_secret_values, name, null) != null &&
      lookup(local.key_vault_secret_ids, name, null) == null
    )
  ]
  container_app_secret_definitions = {
    for name in local.allowed_secret_names :
    name => {
      value               = lookup(local.key_vault_secret_ids, name, null) == null ? lookup(local.direct_secret_values, name, null) : null
      key_vault_secret_id = lookup(local.key_vault_secret_ids, name, null)
    }
    if(
      lookup(local.key_vault_secret_ids, name, null) != null ||
      lookup(local.direct_secret_values, name, null) != null
    )
  }
  registry_password_secret_name = (
    !var.container_registry_use_managed_identity && local.secret_available["registry-password"]
    ? "registry-password"
    : null
  )
  public_network_access = (
    var.container_app_environment_public_network_access != null
    ? var.container_app_environment_public_network_access
    : (
      var.infrastructure_subnet_id != null
      ? "Disabled"
      : "Enabled"
    )
  )

  common_env = {
    APP_VERSION                           = var.app_version
    ENVIRONMENT                           = var.environment
    LOG_LEVEL                             = var.log_level
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    AUTH_MODE                             = var.auth_mode
    AUTH_ISSUER                           = var.auth_issuer
    AUTH_JWKS_URL                         = var.auth_jwks_url
    AUTH_REQUIRED_SCOPE                   = var.auth_required_scope
    AUTH_REQUIRED_AUDIENCE                = var.auth_required_audience
    AUTH_CLOCK_SKEW_SECONDS               = tostring(var.auth_clock_skew_seconds)
    AUTH_SERVICE_TOKEN_URL                = var.auth_service_token_url
    AUTH_SERVICE_CLIENT_ID                = var.auth_service_client_id
    AUTH_SERVICE_SCOPE                    = var.auth_service_scope
    AUTH_SERVICE_AUDIENCE                 = var.auth_service_audience
    ENABLE_LLM                            = tostring(var.enable_llm)
    ENABLE_PII_LOGGING                    = tostring(var.enable_pii_logging)
    SKIP_STARTUP_DEPENDENCY_CHECKS        = tostring(var.skip_startup_dependency_checks)
    SCORING_MODEL_NAME                    = var.scoring_model_name
    SCORING_MODEL_STAGE                   = var.scoring_model_stage
    OTEL_ENABLED                          = tostring(var.otel_enabled)
    OTEL_SAMPLER_RATIO                    = tostring(var.otel_sampler_ratio)
    OTEL_SERVICE_NAMESPACE                = var.otel_service_namespace
  }

  common_secret_env = {
    POSTGRES_DSN               = local.secret_available["postgres-dsn"] ? "postgres-dsn" : null
    RABBITMQ_URL               = local.secret_available["rabbitmq-url"] ? "rabbitmq-url" : null
    AUTH_SERVICE_CLIENT_SECRET = local.secret_available["auth-service-client-secret"] ? "auth-service-client-secret" : null
    AUTH_SHARED_SECRET         = local.secret_available["auth-shared-secret"] ? "auth-shared-secret" : null
  }

  service_apps = {
    "application-api" = {
      app_name         = "${var.project_name}-app-api"
      image_name       = "application"
      external_ingress = false
      cpu              = 0.25
      memory           = "0.5Gi"
      min_replicas     = 1
      max_replicas     = 2
      extra_env        = {}
    }
    "feature-api" = {
      app_name         = "${var.project_name}-feature-api"
      image_name       = "feature"
      external_ingress = false
      cpu              = 0.25
      memory           = "0.5Gi"
      min_replicas     = 1
      max_replicas     = 3
      extra_env        = {}
    }
    "scoring-api" = {
      app_name         = "${var.project_name}-score-api"
      image_name       = "scoring"
      external_ingress = false
      cpu              = 0.5
      memory           = "1.0Gi"
      min_replicas     = 1
      max_replicas     = 3
      extra_env = {
        ARTIFACT_STORAGE_BACKEND                 = "azure_blob"
        ARTIFACT_BLOB_ACCOUNT_URL                = local.artifact_blob_account_url
        ARTIFACT_BLOB_CONTAINER_NAME             = var.artifact_container_name
        ARTIFACT_BLOB_MANAGED_IDENTITY_CLIENT_ID = azurerm_user_assigned_identity.runtime.client_id
      }
      extra_secret_env = {
        MODEL_SIGNING_PUBLIC_KEY_PEM = (
          local.secret_available["model-signing-public-key-pem"]
          ? "model-signing-public-key-pem"
          : null
        )
      }
    }
    "decision-api" = {
      app_name         = "${var.project_name}-decision-api"
      image_name       = "decision"
      external_ingress = false
      cpu              = 0.25
      memory           = "0.5Gi"
      min_replicas     = 1
      max_replicas     = 3
      extra_env        = {}
    }
    "collab-api" = {
      app_name         = "${var.project_name}-collab-api"
      image_name       = "collab-assistant"
      external_ingress = false
      cpu              = 0.25
      memory           = "0.5Gi"
      min_replicas     = 1
      max_replicas     = 2
      extra_env        = {}
    }
    "mlops-api" = {
      app_name         = "${var.project_name}-mlops-api"
      image_name       = "mlops"
      external_ingress = false
      cpu              = 0.5
      memory           = "1.0Gi"
      min_replicas     = 1
      max_replicas     = 2
      extra_env = {
        ARTIFACT_STORAGE_BACKEND                 = "azure_blob"
        ARTIFACT_BLOB_ACCOUNT_URL                = local.artifact_blob_account_url
        ARTIFACT_BLOB_CONTAINER_NAME             = var.artifact_container_name
        ARTIFACT_BLOB_MANAGED_IDENTITY_CLIENT_ID = azurerm_user_assigned_identity.runtime.client_id
      }
      extra_secret_env = {
        MODEL_SIGNING_PRIVATE_KEY_PEM = (
          local.secret_available["model-signing-private-key-pem"]
          ? "model-signing-private-key-pem"
          : null
        )
      }
    }
    "audit-api" = {
      app_name         = "${var.project_name}-audit-api"
      image_name       = "observability-audit"
      external_ingress = false
      cpu              = 0.25
      memory           = "0.5Gi"
      min_replicas     = 1
      max_replicas     = 2
      extra_env        = {}
    }
  }

  background_apps = {
    "feature-worker" = {
      app_name     = "${var.project_name}-feature-wkr"
      image_name   = "feature"
      command      = ["python", "-m", "feature_service.worker"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 3
      extra_env    = {}
    }
    "scoring-worker" = {
      app_name     = "${var.project_name}-score-wkr"
      image_name   = "scoring"
      command      = ["python", "-m", "scoring_service.worker"]
      cpu          = 0.5
      memory       = "1.0Gi"
      min_replicas = 1
      max_replicas = 3
      extra_env = {
        ARTIFACT_STORAGE_BACKEND                 = "azure_blob"
        ARTIFACT_BLOB_ACCOUNT_URL                = local.artifact_blob_account_url
        ARTIFACT_BLOB_CONTAINER_NAME             = var.artifact_container_name
        ARTIFACT_BLOB_MANAGED_IDENTITY_CLIENT_ID = azurerm_user_assigned_identity.runtime.client_id
      }
      extra_secret_env = {
        MODEL_SIGNING_PUBLIC_KEY_PEM = (
          local.secret_available["model-signing-public-key-pem"]
          ? "model-signing-public-key-pem"
          : null
        )
      }
    }
    "decision-worker" = {
      app_name     = "${var.project_name}-decision-wkr"
      image_name   = "decision"
      command      = ["python", "-m", "decision_service.worker"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 3
      extra_env    = {}
    }
    "collab-worker" = {
      app_name     = "${var.project_name}-collab-wkr"
      image_name   = "collab-assistant"
      command      = ["python", "-m", "collab_assistant.worker"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
    "audit-worker" = {
      app_name     = "${var.project_name}-audit-wkr"
      image_name   = "observability-audit"
      command      = ["python", "-m", "observability_audit.worker"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
    "application-relay" = {
      app_name     = "${var.project_name}-app-relay"
      image_name   = "application"
      command      = ["python", "/app/scripts/runtime/run_outbox_relay.py", "--service", "application"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
    "feature-relay" = {
      app_name     = "${var.project_name}-feature-relay"
      image_name   = "feature"
      command      = ["python", "/app/scripts/runtime/run_outbox_relay.py", "--service", "feature"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
    "scoring-relay" = {
      app_name     = "${var.project_name}-score-relay"
      image_name   = "scoring"
      command      = ["python", "/app/scripts/runtime/run_outbox_relay.py", "--service", "scoring"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
    "decision-relay" = {
      app_name     = "${var.project_name}-decision-relay"
      image_name   = "decision"
      command      = ["python", "/app/scripts/runtime/run_outbox_relay.py", "--service", "decision"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
    "collab-relay" = {
      app_name     = "${var.project_name}-collab-relay"
      image_name   = "collab-assistant"
      command      = ["python", "/app/scripts/runtime/run_outbox_relay.py", "--service", "collab-assistant"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
    "mlops-relay" = {
      app_name     = "${var.project_name}-mlops-relay"
      image_name   = "mlops"
      command      = ["python", "/app/scripts/runtime/run_outbox_relay.py", "--service", "mlops"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 1
      max_replicas = 2
      extra_env    = {}
    }
  }
}

check "service_image_references" {
  assert {
    condition = alltrue([
      for image_reference in values(local.service_image_references) :
      can(regex("^[^\\s]+@sha256:[0-9a-f]{64}$", image_reference))
    ])
    error_message = "All deployments must use immutable OCI image references pinned by sha256 digest."
  }
}

check "core_runtime_secrets" {
  assert {
    condition = (
      local.secret_available["postgres-dsn"] &&
      local.secret_available["rabbitmq-url"]
    )
    error_message = "Deployments must provide postgres-dsn and rabbitmq-url via Key Vault secret IDs or non-prod fallback variables."
  }
}

check "key_vault_secret_configuration" {
  assert {
    condition     = length(local.key_vault_secret_ids) == 0 || var.key_vault_id != null
    error_message = "Key Vault secret references require key_vault_id."
  }
}

check "auth_configuration" {
  assert {
    condition = (
      var.auth_mode == "disabled" || (
        var.auth_issuer != null &&
        var.auth_jwks_url != null &&
        var.auth_required_scope != null &&
        var.auth_required_audience != null &&
        var.auth_service_token_url != null &&
        var.auth_service_client_id != null &&
        local.secret_available["auth-service-client-secret"]
      )
    )
    error_message = "Required auth_mode deployments must set issuer, jwks, scope, audience, and service credentials."
  }
}

check "model_signing_configuration" {
  assert {
    condition = (
      local.secret_available["model-signing-private-key-pem"] &&
      local.secret_available["model-signing-public-key-pem"]
    )
    error_message = "Deployments must provide both model-signing-private-key-pem and model-signing-public-key-pem via Key Vault references or non-prod fallback variables."
  }
}

check "registry_configuration" {
  assert {
    condition = (
      var.container_registry_use_managed_identity
      ? (
        var.container_registry_resource_id != null &&
        can(regex("\\.azurecr\\.io$", local.registry_server))
      )
      : (
        var.container_registry_username != null &&
        local.secret_available["registry-password"]
      )
    )
    error_message = "Registry auth must use Azure managed identity with an Azure Container Registry resource ID, or provide username plus registry-password secret."
  }
}

check "production_private_environment" {
  assert {
    condition = (
      var.environment != "prod" || (
        var.infrastructure_subnet_id != null &&
        local.public_network_access == "Disabled"
      )
    )
    error_message = "Production Container Apps deployments must use a delegated subnet and disable public network access on the environment."
  }
}

check "production_auth_required" {
  assert {
    condition     = var.environment != "prod" || var.auth_mode == "required"
    error_message = "Production deployments must require OIDC authentication."
  }
}

check "production_pii_logging_disabled" {
  assert {
    condition     = var.environment != "prod" || !var.enable_pii_logging
    error_message = "Production deployments must keep PII logging disabled."
  }
}

check "production_key_vault_secret_boundary" {
  assert {
    condition = (
      var.environment != "prod" || (
        var.key_vault_id != null &&
        length(local.raw_secret_names) == 0
      )
    )
    error_message = "Production deployments must source runtime and registry secrets from Key Vault references instead of raw Terraform secret values."
  }
}

check "production_gateway_ingress_allowlist" {
  assert {
    condition = (
      var.environment != "prod" ||
      !var.gateway_external_ingress_enabled ||
      length(var.gateway_allowed_cidrs) > 0
    )
    error_message = "Production gateway ingress must define gateway_allowed_cidrs when external ingress is enabled."
  }
}

check "production_telemetry_required" {
  assert {
    condition     = var.environment != "prod" || var.otel_enabled
    error_message = "Production deployments must enable OpenTelemetry export."
  }
}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.project_name}-law"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "${var.project_name}-appi"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.main.id
  sampling_percentage = 100
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "runtime" {
  name                = "${var.project_name}-runtime-uai"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "key_vault_secrets_user" {
  count = var.key_vault_id != null && length(local.key_vault_secret_ids) > 0 ? 1 : 0

  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
}

resource "azurerm_role_assignment" "registry_pull" {
  count = var.container_registry_use_managed_identity && var.container_registry_resource_id != null ? 1 : 0

  scope                = var.container_registry_resource_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
}

resource "azurerm_role_assignment" "artifact_blob_data_contributor" {
  scope                = azurerm_storage_account.artifacts.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
}

resource "azurerm_storage_account" "artifacts" {
  name                            = var.artifact_storage_account_name
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = var.artifact_storage_account_replication_type
  account_kind                    = "StorageV2"
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  local_user_enabled              = false
  shared_access_key_enabled       = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "artifacts" {
  name                  = var.artifact_container_name
  storage_account_id    = azurerm_storage_account.artifacts.id
  container_access_type = "private"
}

resource "azurerm_container_app_environment" "main" {
  name                               = "${var.project_name}-env"
  location                           = azurerm_resource_group.main.location
  resource_group_name                = azurerm_resource_group.main.name
  logs_destination                   = "log-analytics"
  log_analytics_workspace_id         = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id           = var.infrastructure_subnet_id
  infrastructure_resource_group_name = var.infrastructure_resource_group_name
  internal_load_balancer_enabled     = var.infrastructure_subnet_id != null
  public_network_access              = local.public_network_access
  zone_redundancy_enabled            = var.zone_redundancy_enabled && var.infrastructure_subnet_id != null
  tags                               = local.tags

  identity {
    type = "SystemAssigned"
  }

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azapi_update_resource" "main_open_telemetry" {
  type        = "Microsoft.App/managedEnvironments@2024-10-02-preview"
  resource_id = azurerm_container_app_environment.main.id

  body = jsonencode({
    properties = {
      appInsightsConfiguration = {
        connectionString = azurerm_application_insights.main.connection_string
      }
      openTelemetryConfiguration = {
        tracesConfiguration = {
          destinations = ["appInsights"]
        }
      }
    }
  })
}

resource "azurerm_container_app" "service_apps" {
  for_each = local.service_apps

  depends_on = [
    azapi_update_resource.main_open_telemetry,
    azurerm_application_insights.main,
    azurerm_role_assignment.artifact_blob_data_contributor,
    azurerm_role_assignment.key_vault_secrets_user,
    azurerm_role_assignment.registry_pull,
  ]

  name                         = each.value.app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  dynamic "secret" {
    for_each = local.container_app_secret_definitions
    content {
      name                = secret.key
      value               = secret.value.value
      key_vault_secret_id = secret.value.key_vault_secret_id
      identity = (
        secret.value.key_vault_secret_id != null
        ? azurerm_user_assigned_identity.runtime.id
        : null
      )
    }
  }

  registry {
    server               = local.registry_server
    identity             = var.container_registry_use_managed_identity ? azurerm_user_assigned_identity.runtime.id : null
    username             = var.container_registry_use_managed_identity ? null : var.container_registry_username
    password_secret_name = local.registry_password_secret_name
  }

  ingress {
    allow_insecure_connections = false
    external_enabled           = each.value.external_ingress
    target_port                = 8000
    transport                  = "http"

    dynamic "ip_security_restriction" {
      for_each = each.value.external_ingress ? var.gateway_allowed_cidrs : []
      content {
        name             = format("allow-%02d", tonumber(ip_security_restriction.key) + 1)
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
        description      = "Allowed gateway source range"
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = each.value.min_replicas
    max_replicas = each.value.max_replicas

    container {
      name   = each.key
      image  = local.service_image_references[each.value.image_name]
      cpu    = each.value.cpu
      memory = each.value.memory

      dynamic "env" {
        for_each = concat(
          [
            for name, value in merge(local.common_env, each.value.extra_env) :
            {
              name  = name
              value = value
            }
            if value != null
          ],
          [
            for name, secret_name in merge(
              local.common_secret_env,
              lookup(each.value, "extra_secret_env", {}),
            ) :
            {
              name        = name
              secret_name = secret_name
            }
            if secret_name != null
          ],
        )
        content {
          name        = env.value.name
          value       = try(env.value.value, null)
          secret_name = try(env.value.secret_name, null)
        }
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        failure_count_threshold = 10
        interval_seconds        = 10
        timeout                 = 3
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        failure_count_threshold = 3
        interval_seconds        = 10
        timeout                 = 3
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/ready"
        failure_count_threshold = 3
        success_count_threshold = 2
        interval_seconds        = 10
        timeout                 = 3
      }
    }
  }
}

resource "azurerm_container_app" "background_apps" {
  for_each = local.background_apps

  depends_on = [
    azapi_update_resource.main_open_telemetry,
    azurerm_application_insights.main,
    azurerm_role_assignment.artifact_blob_data_contributor,
    azurerm_role_assignment.key_vault_secrets_user,
    azurerm_role_assignment.registry_pull,
  ]

  name                         = each.value.app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  dynamic "secret" {
    for_each = local.container_app_secret_definitions
    content {
      name                = secret.key
      value               = secret.value.value
      key_vault_secret_id = secret.value.key_vault_secret_id
      identity = (
        secret.value.key_vault_secret_id != null
        ? azurerm_user_assigned_identity.runtime.id
        : null
      )
    }
  }

  registry {
    server               = local.registry_server
    identity             = var.container_registry_use_managed_identity ? azurerm_user_assigned_identity.runtime.id : null
    username             = var.container_registry_use_managed_identity ? null : var.container_registry_username
    password_secret_name = local.registry_password_secret_name
  }

  template {
    min_replicas = each.value.min_replicas
    max_replicas = each.value.max_replicas

    container {
      name    = each.key
      image   = local.service_image_references[each.value.image_name]
      cpu     = each.value.cpu
      memory  = each.value.memory
      command = each.value.command

      dynamic "env" {
        for_each = concat(
          [
            for name, value in merge(local.common_env, each.value.extra_env) :
            {
              name  = name
              value = value
            }
            if value != null
          ],
          [
            for name, secret_name in merge(
              local.common_secret_env,
              lookup(each.value, "extra_secret_env", {}),
            ) :
            {
              name        = name
              secret_name = secret_name
            }
            if secret_name != null
          ],
        )
        content {
          name        = env.value.name
          value       = try(env.value.value, null)
          secret_name = try(env.value.secret_name, null)
        }
      }

    }
  }
}

resource "azurerm_container_app" "gateway" {
  depends_on = [
    azurerm_role_assignment.key_vault_secrets_user,
    azurerm_role_assignment.registry_pull,
  ]

  name                         = "${var.project_name}-gateway"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.runtime.id]
  }

  dynamic "secret" {
    for_each = local.container_app_secret_definitions
    content {
      name                = secret.key
      value               = secret.value.value
      key_vault_secret_id = secret.value.key_vault_secret_id
      identity = (
        secret.value.key_vault_secret_id != null
        ? azurerm_user_assigned_identity.runtime.id
        : null
      )
    }
  }

  registry {
    server               = local.registry_server
    identity             = var.container_registry_use_managed_identity ? azurerm_user_assigned_identity.runtime.id : null
    username             = var.container_registry_use_managed_identity ? null : var.container_registry_username
    password_secret_name = local.registry_password_secret_name
  }

  ingress {
    allow_insecure_connections = false
    external_enabled           = var.gateway_external_ingress_enabled
    target_port                = 8000
    transport                  = "http"

    dynamic "ip_security_restriction" {
      for_each = var.gateway_external_ingress_enabled ? var.gateway_allowed_cidrs : []
      content {
        name             = format("allow-%02d", tonumber(ip_security_restriction.key) + 1)
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
        description      = "Allowed gateway source range"
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 2
    max_replicas = 4

    container {
      name   = "gateway"
      image  = local.service_image_references["api-gateway"]
      cpu    = 0.5
      memory = "1.0Gi"

      dynamic "env" {
        for_each = concat(
          [
            for name, value in merge(
              local.common_env,
              {
                FEATURE_SERVICE_URL  = "https://${azurerm_container_app.service_apps["feature-api"].ingress[0].fqdn}"
                SCORING_SERVICE_URL  = "https://${azurerm_container_app.service_apps["scoring-api"].ingress[0].fqdn}"
                DECISION_SERVICE_URL = "https://${azurerm_container_app.service_apps["decision-api"].ingress[0].fqdn}"
              },
            ) :
            {
              name  = name
              value = value
            }
            if value != null
          ],
          [
            for name, secret_name in local.common_secret_env :
            {
              name        = name
              secret_name = secret_name
            }
            if secret_name != null
          ],
        )
        content {
          name        = env.value.name
          value       = try(env.value.value, null)
          secret_name = try(env.value.secret_name, null)
        }
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        failure_count_threshold = 10
        interval_seconds        = 10
        timeout                 = 3
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health"
        failure_count_threshold = 3
        interval_seconds        = 10
        timeout                 = 3
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/ready"
        failure_count_threshold = 3
        success_count_threshold = 2
        interval_seconds        = 10
        timeout                 = 3
      }
    }
  }
}
