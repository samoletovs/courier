// courier — shared NauroLabs email/notification service.
//
// Deploys: Managed Identity, Log Analytics + App Insights, Storage,
// Azure Communication Services (ACS) + Email Communication Services +
// Azure-managed sender domain, a Flex Consumption Function App, and the
// RBAC role assignment that lets the function send email via the identity.
//
// Scope: resource group (courier-rg).
//
// Deploy:
//   az group create -n courier-rg -l northeurope
//   az deployment group create -g courier-rg \
//     -f infrastructure/main.bicep -p infrastructure/main.bicepparam

targetScope = 'resourceGroup'

@description('Short, lowercase tag for resource names.')
@minLength(3)
@maxLength(8)
param namePrefix string = 'courier'

@description('Azure region for the Function App and supporting resources.')
param location string = resourceGroup().location

@description('5-8 char random suffix for globally-unique resource names.')
@minLength(3)
@maxLength(8)
param suffix string

@description('ACS data residency location. Keep email data in the EU.')
@allowed([
  'Europe'
  'United States'
  'Africa'
  'Asia Pacific'
  'Australia'
  'Brazil'
  'Canada'
  'France'
  'Germany'
  'India'
  'Japan'
  'Korea'
  'Norway'
  'Switzerland'
  'UAE'
  'UK'
])
param acsDataLocation string = 'Europe'

@description('Comma-separated recipient allowlist (open-relay guard). Emails or domains.')
param allowedRecipients string

@description('Tags applied to all resources.')
param tags object = {
  project: 'courier'
  owner: 'samoletovs'
  costCenter: 'lab'
  environment: 'prod'
}

// --- Names ------------------------------------------------------------------

var storageName = toLower('st${namePrefix}${suffix}')
var logWorkspaceName = 'log-${namePrefix}'
var appInsightsName = 'appi-${namePrefix}'
var managedIdentityName = 'id-${namePrefix}'
var functionAppName = 'func-${namePrefix}-${suffix}'
var functionPlanName = 'plan-${namePrefix}-${suffix}'
var acsName = 'acs-${namePrefix}-${suffix}'
var emailServiceName = 'acs-email-${namePrefix}-${suffix}'

// Contributor — the documented ACS Entra data-plane role (no narrower email role today).
var contributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')

// --- Managed identity -------------------------------------------------------

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: managedIdentityName
  location: location
  tags: tags
}

// --- Log Analytics + App Insights -------------------------------------------

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logWorkspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: 1
    }
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logWorkspace.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// --- Storage (Function host) ------------------------------------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

// Flex Consumption deployment package container (must exist before publish).
resource appPackageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'app-package'
  properties: {
    publicAccess: 'None'
  }
}

// --- Azure Communication Services + Email -----------------------------------

resource emailService 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: emailServiceName
  location: 'global'
  tags: tags
  properties: {
    dataLocation: acsDataLocation
  }
}

resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  tags: tags
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
}

resource senderUsername 'Microsoft.Communication/emailServices/domains/senderUsernames@2023-04-01' = {
  parent: emailDomain
  name: 'donotreply'
  properties: {
    username: 'donotreply'
    displayName: 'NauroLabs Courier'
  }
}

resource acs 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: acsName
  location: 'global'
  tags: tags
  properties: {
    dataLocation: acsDataLocation
    linkedDomains: [
      emailDomain.id
    ]
  }
}

// Let the function's identity call the ACS data plane (send email) via Entra.
resource acsRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acs
  name: guid(acs.id, uami.id, contributorRoleId)
  properties: {
    roleDefinitionId: contributorRoleId
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- Function App (Flex Consumption) ----------------------------------------

resource functionPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: functionPlanName
  location: location
  tags: tags
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}app-package'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: uami.id
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 512
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: uami.properties.clientId
        }
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storage.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'AzureWebJobsStorage__clientId'
          value: uami.properties.clientId
        }
        {
          name: 'ACS_ENDPOINT'
          value: 'https://${acs.properties.hostName}'
        }
        {
          name: 'SENDER_ADDRESS'
          value: 'donotreply@${emailDomain.properties.fromSenderDomain}'
        }
        {
          name: 'ALLOWED_RECIPIENTS'
          value: allowedRecipients
        }
      ]
    }
  }
}

// Storage RBAC for the Function host identity (shared-key access is disabled).
resource storageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, uami.id, 'storage-blob-data-owner')
  properties: {
    // Storage Blob Data Owner
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- Outputs ----------------------------------------------------------------

output functionAppName string = functionApp.name
output functionHostname string = functionApp.properties.defaultHostName
output acsEndpoint string = 'https://${acs.properties.hostName}'
output senderAddress string = 'donotreply@${emailDomain.properties.fromSenderDomain}'
output managedIdentityClientId string = uami.properties.clientId
