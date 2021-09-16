terraform {
  required_providers {
    azuread = {
      source = "hashicorp/azuread"
      # version = ??
    }
    azurerm = {
      source = "hashicorp/azurerm"
      # version = ??
    }
  }
  # required_version = ">= 1.0.1"
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs
provider "azurerm" {
  # export ARM_SUBSCRIPTION_ID=...
  subscription_id = "TODO"
  features {}
}

variable "azure_ad_tenant_id" {
  type    = string
  default = "TODO"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "proj" {
  type    = string
  default = "fnhw09"
}

variable "owner" {
  type    = string
  default = "TODO"
}

variable "location" {
  type    = string
  default = "West US 2"
}


# TODO: should probably create a resource group to put KVs in instead of hardcoding like this :)
variable "kv_creation_azure_subscription_id" {
  description = "Where should we create these KVs?"
  type        = string
  default     = "TODO"
}

variable "kv_creation_azure_tenant_id" {
  description = "Where should we create these KVs?"
  type        = string
  default     = "TODO"
}

variable "kv_creation_resource_group_name" {
  description = "Where should we create these KVs?"
  type        = string
  default     = "fnhw09-01-rg-dev-bbk"
}

variable "kv_creation_location" {
  description = "Where should we create these KVs?"
  type        = string
  default     = "westus2"
}

resource "azurerm_resource_group" "rg" {
  name     = "${var.proj}-01-rg-${var.env}-${var.owner}"
  location = var.location
  tags     = {}
}

resource "azurerm_storage_account" "sa" {
  name                     = "${var.proj}01sa${var.env}${var.owner}"
  location                 = var.location
  account_replication_type = "LRS"
  account_tier             = "Standard"
  resource_group_name      = azurerm_resource_group.rg.name
  tags                     = {}
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/application_insights
resource "azurerm_application_insights" "ai" {
  name                = "${var.proj}-01-ai-${var.env}-${var.owner}"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  application_type    = "other"
  tags                = {}
}


# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/app_service_plan
resource "azurerm_app_service_plan" "spl" {
  name                         = "${var.proj}-01-spl-${var.env}-${var.owner}"
  is_xenon                     = false
  maximum_elastic_worker_count = 1
  location                     = var.location
  per_site_scaling             = false
  resource_group_name          = azurerm_resource_group.rg.name
  kind                         = "functionapp"
  reserved                     = true

  sku {
    capacity = 0
    size     = "Y1"
    tier     = "Dynamic"
  }
  tags = {}
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/function_app
resource "azurerm_function_app" "fa" {
  name                = "${var.proj}-01-fa-${var.env}-${var.owner}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  app_service_plan_id = azurerm_app_service_plan.spl.id
  app_settings = {
    "APPINSIGHTS_INSTRUMENTATIONKEY"           = azurerm_application_insights.ai.instrumentation_key,
    "FUNCTIONS_WORKER_RUNTIME"                 = "python",
    "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET" = azuread_application_password.ad_app_pass.value
    "WEBSITE_RUN_FROM_PACKAGE"                 = "",

    # app specific variables
    "KV_CREATION_AZURE_SUBSCRIPTION_ID" = var.kv_creation_azure_subscription_id
    "KV_CREATION_AZURE_TENANT_ID"       = var.kv_creation_azure_tenant_id
    "KV_CREATION_LOCATION"              = var.kv_creation_location
    "KV_CREATION_RESOURCE_GROUP_NAME"   = var.kv_creation_resource_group_name
  }
  auth_settings {
    enabled                       = true
    issuer                        = "https://sts.windows.net/${var.azure_ad_tenant_id}/v2.0"
    token_refresh_extension_hours = 0
    token_store_enabled           = true
    unauthenticated_client_action = "RedirectToLoginPage"
    active_directory {
      allowed_audiences = [
        "api://${azuread_application.ad_app.application_id}"
      ]
      client_id = azuread_application.ad_app.application_id
    }
  }

  identity {
    type = "SystemAssigned"
  }

  os_type = "linux"
  site_config {
    linux_fx_version          = "python|3.9"
    use_32_bit_worker_process = false
  }
  storage_account_name       = azurerm_storage_account.sa.name
  storage_account_access_key = azurerm_storage_account.sa.primary_access_key
  lifecycle {
    ignore_changes = [
      app_settings["WEBSITE_RUN_FROM_PACKAGE"],
    ]
  }
  tags    = {}
  version = "~3"  # If you leave off the version, it defaults to 1, and then trying to upload a Python 3.9 app has a really bad error message
}

# https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/application
# This is so users can log into our app
resource "azuread_application" "ad_app" {
  display_name = "${var.proj}-01-adapp-${var.env}-${var.owner}"

  required_resource_access {
    # Microsoft Graph App ID
    # az ad sp list --display-name "Microsoft Graph" --query '[].{appDisplayName:appDisplayName, appId:appId}'
    # this is stable across tenants
    resource_app_id = "00000003-0000-0000-c000-000000000000"

    resource_access {
      # Sign in and read user profile
      # Source: googling :/
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"
      type = "Scope"
    }
  }

  web {
    # We can't use azurerm_function_app.fa.default_hostname because it creates a cycle
    redirect_uris = ["https://${var.proj}-01-fa-${var.env}-${var.owner}.azurewebsites.net/.auth/login/aad/callback"]
    implicit_grant {
      access_token_issuance_enabled = false
    }
  }
}

# https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/application_password
resource "azuread_application_password" "ad_app_pass" {
  application_object_id = azuread_application.ad_app.object_id
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment
# This is so our app can create KVs
resource "azurerm_role_assignment" "fa_ra" {
  principal_id         = azurerm_function_app.fa.identity.0.principal_id
  scope                = azurerm_resource_group.rg.id # TODO: gonna use the same RG for creating KVs. In real life, this would be
  role_definition_name = "Key Vault Contributor"      # TODO: I would really only like this to be able to create key vaults. Let's see if this works :)
}


# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/log_analytics_workspace
resource "azurerm_log_analytics_workspace" "law" {
  name                = "${var.proj}-01-law-${var.env}-${var.owner}"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "pergb2018"
  retention_in_days   = 30
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/monitor_diagnostic_setting
# Tell our azure function that it needs to log to the log analytics workspace
resource "azurerm_monitor_diagnostic_setting" "mds" {
  name               = "${var.proj}-01-law-${var.env}-${var.owner}"
  target_resource_id = azurerm_function_app.fa.id
  # storage_account_id = azurerm_storage_account.sa.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id

  log {
    category = "FunctionAppLogs"
    enabled  = true

    retention_policy {
      enabled = false
    }
  }
  metric {
    category = "AllMetrics"
    enabled  = false

    retention_policy {
      days    = 0
      enabled = false
    }
  }

}

output "function_app_name" {
  value       = azurerm_function_app.fa.name
  description = "Deployed function app name"
}

output "function_app_hostname" {
  value       = azurerm_function_app.fa.default_hostname
  description = "Deployed function app hostname"
}
