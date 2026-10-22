#!/usr/bin/env bash
# Authenticated cloud operations: never invoked by validation/CI.
set -euo pipefail
cd "$(dirname "$0")/.."
operation="${1:-preview}"
: "${RESOURCE_GROUP:?Set RESOURCE_GROUP to the existing sandbox resource group}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID}"
prefix="${PREFIX:-orderlab}"
operator="${OPERATOR_OBJECT_ID:-}"
principal_type="${OPERATOR_PRINCIPAL_TYPE:-User}"
[[ "$operation" == preview || "$operation" == apply ]] || { echo 'Use preview or apply' >&2; exit 2; }
[[ "$prefix" =~ ^[a-z][a-z0-9]{2,11}$ ]] || { echo 'PREFIX: 3..12 lowercase letters/digits, starting with a letter' >&2; exit 2; }
[[ "$RESOURCE_GROUP" =~ ^[a-zA-Z0-9_.()-]{1,90}$ ]] || exit 2
if [[ -n "$operator" && ! "$operator" =~ ^[a-fA-F0-9-]{36}$ ]]; then echo 'Invalid operator object ID' >&2; exit 2; fi
[[ "$principal_type" == User || "$principal_type" == ServicePrincipal || "$principal_type" == Group ]] || exit 2
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az group show --name "$RESOURCE_GROUP" --output none
params=(prefix="$prefix" operatorObjectId="$operator" operatorPrincipalType="$principal_type")
if [[ "$operation" == preview ]]; then
  az deployment group what-if --resource-group "$RESOURCE_GROUP" --template-file infra/main.bicep --parameters "${params[@]}"
  echo 'Foundation preview only. Worker preview requires an existing foundation and built image.'
  exit 0
fi
[[ "${CONFIRM_DEPLOY:-}" == "$RESOURCE_GROUP" ]] || { echo 'Set CONFIRM_DEPLOY to the exact sandbox resource group name' >&2; exit 2; }
mkdir -p .deploy
az deployment group create --name orders-foundation --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep --parameters "${params[@]}" --query properties.outputs --output json > .deploy/outputs.json
acr=$(python -c "import json; print(json.load(open('.deploy/outputs.json'))['registryName']['value'])")
server=$(python -c "import json; print(json.load(open('.deploy/outputs.json'))['registryServer']['value'])")
# Requiring a clean git tree binds the image tag and deployed code to the commit.
[[ -z "$(git status --porcelain)" ]] || { echo 'Commit changes before building a release image' >&2; exit 2; }
tag=$(git rev-parse HEAD)
az acr build --registry "$acr" --image "orders:$tag" --file Dockerfile .
digest=$(az acr repository show --name "$acr" --image "orders:$tag" --query digest --output tsv)
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'Registry did not return an immutable image digest' >&2; exit 1; }
image="$server/orders@$digest"
printf '%s\n' "$image" > .deploy/image.txt
az deployment group what-if --resource-group "$RESOURCE_GROUP" --template-file infra/worker.bicep \
  --parameters prefix="$prefix" image="$image"
az deployment group create --name orders-worker --resource-group "$RESOURCE_GROUP" \
  --template-file infra/worker.bicep --parameters prefix="$prefix" image="$image" --output none
echo "Deployed worker image: $image"
echo 'RBAC propagation and scale-to-zero cold starts can delay first processing; inspect worker logs.'
