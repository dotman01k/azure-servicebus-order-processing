"""Azure SDK wiring, separate from deterministic domain behavior."""
import os
import re
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.servicebus import ServiceBusClient
from azure.data.tables import TableClient


def configuration():
    namespace = os.environ.get('SERVICEBUS_NAMESPACE', '')
    endpoint = os.environ.get('TABLE_ENDPOINT', '')
    queue = os.environ.get('QUEUE_NAME', 'orders')
    table = os.environ.get('TABLE_NAME', 'OrderLedger')
    if not re.fullmatch(r'[a-zA-Z0-9-]+\.servicebus\.windows\.net', namespace):
        raise ValueError('SERVICEBUS_NAMESPACE must be the fully qualified commercial Azure namespace')
    if not re.fullmatch(r'https://[a-z0-9]{3,24}\.table\.core\.windows\.net/?', endpoint):
        raise ValueError('TABLE_ENDPOINT must be an HTTPS Azure Table endpoint')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', queue) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{2,62}', table):
        raise ValueError('Invalid queue or table name')
    return namespace, endpoint.rstrip('/'), queue, table


def credential():
    client = os.environ.get('AZURE_CLIENT_ID')
    if client:
        return ManagedIdentityCredential(client_id=client)
    # Explicit local developer login. Never silently fall back to a client secret.
    return AzureCliCredential()


def clients(cred):
    namespace, endpoint, queue, table = configuration()
    return ServiceBusClient(namespace, cred), TableClient(endpoint, table, credential=cred), queue
