output "resource_group_name" {
  description = "Azure resource group hosting the Container Apps platform."
  value       = azurerm_resource_group.main.name
}

output "container_app_environment_id" {
  description = "Managed environment resource ID."
  value       = azurerm_container_app_environment.main.id
}

output "container_app_environment_default_domain" {
  description = "Default domain assigned to the managed environment."
  value       = azurerm_container_app_environment.main.default_domain
}

output "application_insights_id" {
  description = "Application Insights resource receiving managed OpenTelemetry traces."
  value       = azurerm_application_insights.main.id
}

output "application_insights_name" {
  description = "Application Insights resource name for trace analysis and app map."
  value       = azurerm_application_insights.main.name
}

output "runtime_user_assigned_identity_id" {
  description = "Shared runtime user-assigned identity attached to platform workloads."
  value       = azurerm_user_assigned_identity.runtime.id
}

output "runtime_user_assigned_identity_principal_id" {
  description = "Principal ID for the shared runtime user-assigned identity."
  value       = azurerm_user_assigned_identity.runtime.principal_id
}

output "key_vault_id" {
  description = "Configured Key Vault scope for runtime secret references."
  value       = var.key_vault_id
}

output "artifact_storage_account_name" {
  description = "Storage Account hosting the shared model artifact container."
  value       = azurerm_storage_account.artifacts.name
}

output "artifact_blob_account_url" {
  description = "Blob Storage account URL used by scoring and MLOps workloads."
  value       = format("https://%s.blob.core.windows.net", azurerm_storage_account.artifacts.name)
}

output "artifact_container_name" {
  description = "Private Blob container storing model artifacts and model cards."
  value       = azurerm_storage_container.artifacts.name
}

output "runtime_user_assigned_identity_client_id" {
  description = "Client ID for the shared runtime user-assigned identity."
  value       = azurerm_user_assigned_identity.runtime.client_id
}

output "gateway_fqdn" {
  description = "Gateway ingress FQDN."
  value       = azurerm_container_app.gateway.ingress[0].fqdn
}

output "service_app_fqdns" {
  description = "Internal service ingress FQDNs for API workloads."
  value = {
    for name, app in azurerm_container_app.service_apps :
    name => app.ingress[0].fqdn
  }
}

output "background_app_ids" {
  description = "Background worker and relay Container App resource IDs."
  value = {
    for name, app in azurerm_container_app.background_apps :
    name => app.id
  }
}
