"""Security assertions against compiled ARM, independent of Bicep source text."""
import json
import sys
from pathlib import Path


def check(foundation,worker):
    def one(kind):
        found=[r for r in foundation['resources'] if r['type'].lower()==kind.lower()]
        assert len(found)==1,(kind,len(found))
        return found[0]
    bus=one('Microsoft.ServiceBus/namespaces');assert bus['properties']['disableLocalAuth'] is True
    assert bus['sku']['name']=='Standard'
    queue=one('Microsoft.ServiceBus/namespaces/queues')['properties']
    assert queue['requiresDuplicateDetection'] and queue['maxDeliveryCount']==10
    assert queue['deadLetteringOnMessageExpiration'] and queue['lockDuration']=='PT1M'
    storage=one('Microsoft.Storage/storageAccounts')['properties']
    assert storage['allowSharedKeyAccess'] is False and storage['minimumTlsVersion']=='TLS1_2'
    assert storage['allowBlobPublicAccess'] is False
    assert one('Microsoft.ContainerRegistry/registries')['properties']['adminUserEnabled'] is False
    assert one('Microsoft.Insights/metricAlerts')['properties']['criteria']['allOf'][0]['metricName']=='DeadletteredMessages'
    grants=[r for r in foundation['resources'] if r['type']=='Microsoft.Authorization/roleAssignments']
    assert len(grants)==6 and all('scope' in g for g in grants)
    app=next(r for r in worker['resources'] if r['type']=='Microsoft.App/containerApps')
    assert app['identity']['type']=='UserAssigned' and 'ingress' not in app['properties']['configuration']
    assert app['properties']['configuration']['activeRevisionsMode']=='Single'
    scale=app['properties']['template']['scale']
    assert scale['minReplicas']==0 and worker['parameters']['maxReplicas']['maxValue']==5
    rule=scale['rules'][0]['custom']
    assert rule['type']=='azure-servicebus' and rule['identity'] and rule['metadata']['messageCount']=='5'
    assert 'auth' not in rule
    env=app['properties']['template']['containers'][0]['env']
    assert {e['name'] for e in env}=={'AZURE_CLIENT_ID','SERVICEBUS_NAMESPACE','QUEUE_NAME','TABLE_ENDPOINT','TABLE_NAME'}
    print('Compiled template security and deployment contracts passed')


if __name__=='__main__':check(*(json.loads(Path(p).read_text()) for p in sys.argv[1:]))
