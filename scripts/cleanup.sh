#!/usr/bin/env bash
set -euo pipefail
: "${RESOURCE_GROUP:?Set the dedicated project resource group}"
: "${AZURE_SUBSCRIPTION_ID:?Set the project subscription}"
[[ "${CONFIRM_DELETE:-}" == "$RESOURCE_GROUP" ]] || { echo 'Set CONFIRM_DELETE to the exact dedicated project resource group' >&2; exit 2; }
project=$(az group show --subscription "$AZURE_SUBSCRIPTION_ID" --name "$RESOURCE_GROUP" --query tags.project --output tsv)
[[ "$project" == servicebus-order-processing ]] || { echo 'Resource group ownership tag does not match' >&2; exit 2; }
az resource list --subscription "$AZURE_SUBSCRIPTION_ID" --resource-group "$RESOURCE_GROUP" --query '[].{Name:name,Type:type}' --output table
echo 'Deleting this entire dedicated resource group, including queued messages and ledger data.'
az group delete --subscription "$AZURE_SUBSCRIPTION_ID" --name "$RESOURCE_GROUP" --yes
az group exists --subscription "$AZURE_SUBSCRIPTION_ID" --name "$RESOURCE_GROUP"
