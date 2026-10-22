# Validation evidence

Development checks on 2026-10-22:

| Check | Evidence and scope |
| --- | --- |
| Python behavior tests | 42 tests, including 100 concurrent duplicate deliveries, event/order conflicts, a lost commit response, crash after persistence, replay send/completion failures and actual SDK DATA-message encoding. |
| Dependency integrity | Full Python 3.12 transitive lock installed successfully with pip --require-hashes. |
| Bicep compilation | main.bicep and worker.bicep compile with Bicep 0.48.1. |
| Compiled ARM checks | Local/shared-key auth disabled, scoped roles, queue reliability settings, MI scaling, no app ingress, replica cap and dead-letter alert. |
| Shell syntax | bash -n for every operational script. |
| Workflow syntax | Actionlint v1.7.12 for validation, manual deployment and OIDC claims inspection workflows. |

The detailed local command output is in local-tests.txt. Cloud SDK adapters use a fake Table
transaction fixture with real SDK exception classes. This exercises the adapter's reconciliation
branches; it does not emulate Azure's network, authorization, consistency implementation or ARM
provider acceptance. Actual wire DATA-body construction is tested with azure-servicebus 7.14.2.

Live Azure acceptance checks remain operator actions in operations.md. Docker image build,
ACR Tasks subscription capability, MI token acquisition, OIDC federation, actual scaling,
platform quotas, service availability and Azure provider what-if/apply are not established by
the local checks. GitHub CI reports its own independently executed tests on the published commit.

Checks do not make an exactly-once delivery claim. External side effects, automatic retention,
regional failover, private networking and enterprise throughput testing are outside this scope.
