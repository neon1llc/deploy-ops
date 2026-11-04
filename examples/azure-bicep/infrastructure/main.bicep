// infrastructure/main.bicep - Create a private application in an existing Container Apps environment.


// Preserve the template purpose in generated JSON without nonstandard JSON comments
metadata description = 'Create a private application in an existing Container Apps environment.'


// The operator selects the existing environment and an immutable public image
@description('Name of the application to create or update.')
param applicationName string

@description('Azure region of the existing managed environment.')
param location string

@description('Resource ID of an existing, approved Container Apps managed environment.')
param environmentId string

@description('Public container image pinned by its sha256 digest.')
param image string


// A single private replica has no public ingress or implicit registry credentials
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: applicationName
  location: location
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
    }
    template: {
      containers: [
        {
          name: 'web'
          image: image
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}


// These public coordinates feed an independent application release
output name string = app.name
output resource_group string = resourceGroup().name
output subscription_id string = subscription().subscriptionId
