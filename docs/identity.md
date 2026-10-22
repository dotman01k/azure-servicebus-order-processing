# Deployment identity and federation setup

These are optional live-deployment prerequisites. Source and local validation work independently.
Use a dedicated sandbox resource group; do not reuse a subscription-wide deployment identity.
The bootstrap operator needs permission to create an Entra app/service principal and role
assignments. An admin may need to perform these steps if your tenant restricts registrations.

## 1. Protect the GitHub environment

Create environment `azure-dev` in this repository. Restrict deployment branches to main and
configure required reviewers when available for your GitHub plan. The workflow's branch check
is an additional guard. An environment without reviewers does not automatically require approval.

Run **Inspect deployment federation subject** from main. Its job requests a short-lived GitHub
OIDC token and prints ONLY issuer, audience and subject. It makes no Azure calls. Copy the exact
sub claim; default subjects may include immutable owner/repository IDs or organization-specific
templates. Do not invent a legacy repo:name subject or print the full JWT. The inspection and
deployment workflows use the same azure-dev environment, so their environment subject context
matches. If your customized subject includes the workflow identity too, configure separate exact
federated credentials for both workflows or inspect the deployment workflow's actual token.

## 2. Create the principal and federation

From a trusted operator shell after creating the dedicated resource group in the README:

```bash
export REPO='dotman01k/azure-servicebus-order-processing'
export OIDC_SUBJECT='<exact sub printed by the claims workflow>'
export AZURE_SUBSCRIPTION_ID='<sandbox-subscription-id>'
export RESOURCE_GROUP='rg-orders-dev'
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
export TENANT_ID="$(az account show --query tenantId -o tsv)"
export CLIENT_ID="$(az ad app create --display-name orders-dev-infra-ci --query appId -o tsv)"
export PRINCIPAL_ID="$(az ad sp create --id "$CLIENT_ID" --query id -o tsv)"
export RG_SCOPE="$(az group show --name "$RESOURCE_GROUP" --query id -o tsv)"
python - <<'PY'
import json, os
with open('federation.json','w') as f:
    json.dump({'name':'github-orders-dev',
      'issuer':'https://token.actions.githubusercontent.com',
      'subject':os.environ['OIDC_SUBJECT'],
      'audiences':['api://AzureADTokenExchange']}, f)
PY
az ad app federated-credential create --id "$CLIENT_ID" --parameters federation.json
rm federation.json
az role assignment create --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal --role Contributor --scope "$RG_SCOPE"
az role assignment create --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role 'Role Based Access Control Administrator' --scope "$RG_SCOPE"
```

The CI identity's RG-scoped RBAC administrator role can grant permissions within that RG. Use
conditions or an administrator-controlled bootstrap for stricter organizations. It has no
subscription role assignment from these commands. Allow Entra and RBAC propagation before apply.
Provider registration is a separate subscription-operator responsibility.

## 3. Set non-secret GitHub environment variables

```bash
gh variable set AZURE_CLIENT_ID --repo "$REPO" --env azure-dev --body "$CLIENT_ID"
gh variable set AZURE_TENANT_ID --repo "$REPO" --env azure-dev --body "$TENANT_ID"
gh variable set AZURE_SUBSCRIPTION_ID --repo "$REPO" --env azure-dev --body "$AZURE_SUBSCRIPTION_ID"
gh variable set RESOURCE_GROUP --repo "$REPO" --env azure-dev --body "$RESOURCE_GROUP"
gh variable set PREFIX --repo "$REPO" --env azure-dev --body orderlab
# Optional operator demo/replay rights:
export OPERATOR_ID="$(az ad signed-in-user show --query id -o tsv)"
gh variable set OPERATOR_OBJECT_ID --repo "$REPO" --env azure-dev --body "$OPERATOR_ID"
gh variable set OPERATOR_PRINCIPAL_TYPE --repo "$REPO" --env azure-dev --body User
```

Do not add AZURE_CLIENT_SECRET or connection strings. azure/login uses OIDC, and the worker
uses its assigned identity. If retiring the project, remove the bootstrap app/federated
credential deliberately after identifying its exact CLIENT_ID; never delete apps by a broad
name match. Deleting the workload RG does not remove the Entra app registration.
