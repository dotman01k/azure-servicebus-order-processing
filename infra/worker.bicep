targetScope = 'resourceGroup'
@minLength(3)
@maxLength(12)
param prefix string = 'orderlab'
param location string = resourceGroup().location
@description('Immutable image from this project registry: <loginServer>/orders@sha256:<digest>')
param image string
@minValue(1)
@maxValue(5)
param maxReplicas int = 3
var suffix = uniqueString(resourceGroup().id, prefix)
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: '${prefix}-worker-id'
}
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: '${prefix}-env'
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: '${prefix}${suffix}acr'
}
resource bus 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = {
  name: '${prefix}-${suffix}-sb'
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: '${take(prefix, 8)}${suffix}st'
}
resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: '${prefix}-worker'
  location: location
  tags: { project: 'servicebus-order-processing', environment: 'dev' }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      // Background worker: no ingress and no public HTTP endpoint.
    }
    template: {
      containers: [{
        name: 'orders'
        image: image
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          { name: 'SERVICEBUS_NAMESPACE', value: '${bus.name}.servicebus.windows.net' }
          { name: 'QUEUE_NAME', value: 'orders' }
          { name: 'TABLE_ENDPOINT', value: storage.properties.primaryEndpoints.table }
          { name: 'TABLE_NAME', value: 'OrderLedger' }
        ]
      }]
      scale: {
        minReplicas: 0
        maxReplicas: maxReplicas
        pollingInterval: 30
        cooldownPeriod: 300
        rules: [{
          name: 'servicebus-orders'
          custom: {
            type: 'azure-servicebus'
            identity: identity.id
            metadata: { namespace: bus.name, queueName: 'orders', messageCount: '5' }
          }
        }]
      }
    }
  }
}
output appName string = app.name
output image string = image
