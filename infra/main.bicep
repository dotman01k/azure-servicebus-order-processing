targetScope = 'resourceGroup'

@minLength(3)
@maxLength(12)
@description('Lowercase alphanumeric prefix. The deployment script validates this contract.')
param prefix string = 'orderlab'
param location string = resourceGroup().location
@description('Optional existing operator object ID for sending, replaying and inspecting demo orders.')
param operatorObjectId string = ''
@allowed(['User', 'Group', 'ServicePrincipal'])
param operatorPrincipalType string = 'User'

var suffix = uniqueString(resourceGroup().id, prefix)
var tags = { project: 'servicebus-order-processing', environment: 'dev' }
var receiverRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
var senderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
var tableContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
var tableReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '76199698-9eea-4c19-bc75-cec21354c6b6')

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-worker-id'
  location: location
  tags: tags
}
resource bus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: '${prefix}-${suffix}-sb'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: bus
  name: 'orders'
  properties: {
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    defaultMessageTimeToLive: 'P1D'
    deadLetteringOnMessageExpiration: true
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT10M'
    enablePartitioning: false
    maxSizeInMegabytes: 1024
  }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${take(prefix, 8)}${suffix}st'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Enabled'
  }
}
resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource ledger 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'OrderLedger'
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${prefix}${suffix}acr'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 }
  }
}
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}
resource receiverGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, identity.id, receiverRole)
  scope: queue
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: receiverRole }
}
resource tableGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ledger.id, identity.id, tableContributorRole)
  scope: ledger
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: tableContributorRole }
}
resource pullGrant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'AcrPull')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource operatorSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(operatorObjectId)) {
  name: guid(queue.id, operatorObjectId, senderRole)
  scope: queue
  properties: { principalId: operatorObjectId, principalType: operatorPrincipalType, roleDefinitionId: senderRole }
}
resource operatorReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(operatorObjectId)) {
  name: guid(queue.id, operatorObjectId, receiverRole)
  scope: queue
  properties: { principalId: operatorObjectId, principalType: operatorPrincipalType, roleDefinitionId: receiverRole }
}
resource operatorReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(operatorObjectId)) {
  name: guid(ledger.id, operatorObjectId, tableReaderRole)
  scope: ledger
  properties: { principalId: operatorObjectId, principalType: operatorPrincipalType, roleDefinitionId: tableReaderRole }
}
output namespace string = '${bus.name}.servicebus.windows.net'
output queueName string = queue.name
output tableEndpoint string = storage.properties.primaryEndpoints.table
output tableName string = ledger.name
output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output workerIdentityId string = identity.id
output workerIdentityClientId string = identity.properties.clientId
output environmentName string = environment.name
output workspaceId string = logs.properties.customerId

// Fires in Azure Monitor; attach your own action group for notifications.
resource dlqAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-deadletter-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Orders are in the dead-letter queue; inspect before replaying.'
    severity: 2
    enabled: true
    scopes: [bus.id]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        name: 'deadletters'
        criterionType: 'StaticThresholdCriterion'
        metricNamespace: 'Microsoft.ServiceBus/namespaces'
        metricName: 'DeadletteredMessages'
        timeAggregation: 'Maximum'
        operator: 'GreaterThan'
        threshold: 0
        dimensions: [{ name: 'EntityName', operator: 'Include', values: ['orders'] }]
      }]
    }
    autoMitigate: true
    actions: []
  }
}
